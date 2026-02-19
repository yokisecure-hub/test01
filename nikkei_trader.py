#!/usr/bin/env python3
"""
日経225先物ミニ AI相場判定CLIアプリケーション

複数サイトから非同期並列でリアルタイム情報をスクレイピングし、
Claude APIで超短期トレンドを判定する。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import anthropic
from dotenv import load_dotenv
from playwright.async_api import async_playwright, Page, BrowserContext

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
MODEL_NAME: str = "claude-3-5-sonnet-latest"

# Playwright settings
BROWSER_TIMEOUT_MS: int = 30_000  # per-page navigation timeout
SELECTOR_TIMEOUT_MS: int = 15_000  # wait_for_selector timeout
USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ANSI helpers (minimal, no external dependency)
# ---------------------------------------------------------------------------

BOLD = "\033[1m"
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
RESET = "\033[0m"
DIM = "\033[2m"
SEPARATOR = f"{DIM}{'─' * 72}{RESET}"


def _color_verdict(text: str) -> str:
    """AIの判定結果に応じて色を付ける。"""
    if "【買い】" in text:
        return text.replace("【買い】", f"{GREEN}{BOLD}【買い】{RESET}")
    if "【売り】" in text:
        return text.replace("【売り】", f"{RED}{BOLD}【売り】{RESET}")
    if "【様子見】" in text:
        return text.replace("【様子見】", f"{YELLOW}{BOLD}【様子見】{RESET}")
    return text


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ScrapeResult:
    """1サイト分のスクレイピング結果。"""

    source: str
    url: str
    content: str = ""
    error: Optional[str] = None
    elapsed_sec: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.content.strip())


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------


class Scraper:
    """各サイトに最適化された非同期スクレイピングロジックを提供する。"""

    @staticmethod
    async def _new_page(context: BrowserContext) -> Page:
        page = await context.new_page()
        page.set_default_timeout(BROWSER_TIMEOUT_MS)
        return page

    # -- 世界の株価 (nikkei225jp.com) --
    @staticmethod
    async def scrape_nikkei225jp(context: BrowserContext) -> ScrapeResult:
        result = ScrapeResult(source="世界の株価", url="https://nikkei225jp.com/")
        t0 = asyncio.get_event_loop().time()
        page: Optional[Page] = None
        try:
            page = await Scraper._new_page(context)
            await page.goto(result.url, wait_until="domcontentloaded")

            # メインの株価テーブルが描画されるまで待機
            await page.wait_for_selector(
                "#all-idx", timeout=SELECTOR_TIMEOUT_MS
            )

            # 主要指数テーブルからテキストを取得
            sections: list[str] = []

            # 日経225先物・主要指数エリア
            main_el = await page.query_selector("#all-idx")
            if main_el:
                text = await main_el.inner_text()
                sections.append(text.strip())

            # 為替情報
            fx_el = await page.query_selector("#all-fx")
            if fx_el:
                text = await fx_el.inner_text()
                sections.append(f"[為替]\n{text.strip()}")

            result.content = "\n\n".join(sections) if sections else ""

        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            logger.warning("世界の株価 スクレイピング失敗: %s", result.error)
        finally:
            if page:
                await page.close()
            result.elapsed_sec = asyncio.get_event_loop().time() - t0
        return result

    # -- 株探 トップニュース (kabutan.jp) --
    @staticmethod
    async def scrape_kabutan(context: BrowserContext) -> ScrapeResult:
        result = ScrapeResult(source="株探", url="https://kabutan.jp/")
        t0 = asyncio.get_event_loop().time()
        page: Optional[Page] = None
        try:
            page = await Scraper._new_page(context)
            await page.goto(result.url, wait_until="domcontentloaded")

            await page.wait_for_selector(
                ".top_news", timeout=SELECTOR_TIMEOUT_MS
            )

            sections: list[str] = []

            # トップニュース一覧
            news_el = await page.query_selector(".top_news")
            if news_el:
                text = await news_el.inner_text()
                sections.append(f"[トップニュース]\n{text.strip()}")

            # マーケット情報があれば取得
            market_el = await page.query_selector(".top_market")
            if market_el:
                text = await market_el.inner_text()
                sections.append(f"[マーケット]\n{text.strip()}")

            result.content = "\n\n".join(sections) if sections else ""

        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            logger.warning("株探 スクレイピング失敗: %s", result.error)
        finally:
            if page:
                await page.close()
            result.elapsed_sec = asyncio.get_event_loop().time() - t0
        return result

    # -- Investing.com 経済指標カレンダー --
    @staticmethod
    async def scrape_investing(context: BrowserContext) -> ScrapeResult:
        result = ScrapeResult(
            source="Investing.com 経済指標",
            url="https://jp.investing.com/economic-calendar/",
        )
        t0 = asyncio.get_event_loop().time()
        page: Optional[Page] = None
        try:
            page = await Scraper._new_page(context)
            await page.goto(result.url, wait_until="domcontentloaded")

            # 経済指標テーブルの読み込み待機
            await page.wait_for_selector(
                "#economicCalendarData", timeout=SELECTOR_TIMEOUT_MS
            )

            table_el = await page.query_selector("#economicCalendarData")
            if table_el:
                text = await table_el.inner_text()
                # 行数が多すぎる場合は先頭部分のみ (トークン節約)
                lines = text.strip().splitlines()
                if len(lines) > 80:
                    lines = lines[:80]
                    lines.append("... (以下省略)")
                result.content = "\n".join(lines)

        except Exception as exc:
            result.error = f"{type(exc).__name__}: {exc}"
            logger.warning("Investing.com スクレイピング失敗: %s", result.error)
        finally:
            if page:
                await page.close()
            result.elapsed_sec = asyncio.get_event_loop().time() - t0
        return result


# ---------------------------------------------------------------------------
# Analyzer (Claude API)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = textwrap.dedent("""\
    あなたは優秀なデイトレーダーです。
    Playwrightが取得した以下の最新リアルタイム情報から、
    現在の日経225先物ミニの超短期トレンドを【買い】【売り】【様子見】で判定し、
    その根拠を簡潔に箇条書きで出力してください。

    分析の観点:
    - テクニカル: 主要指数の価格推移・前日比・勢い
    - ファンダメンタル: ニュースヘッドライン・経済指標の結果
    - 外部環境: 為替 (USD/JPY)・米国株先物・VIX 等
    - リスク要因: 注意すべきイベントや急変リスク

    出力フォーマット:
    ■ 判定: 【買い】 or 【売り】 or 【様子見】
    ■ 確信度: (高/中/低)
    ■ 根拠:
      - ...
      - ...
    ■ リスク要因:
      - ...
    ■ 推奨戦略: (エントリー方向・利確/損切り目安など簡潔に)
""")


class Analyzer:
    """Anthropic Claude APIを用いた相場分析エンジン。"""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY が設定されていません")
        self._client = anthropic.Anthropic(api_key=api_key)

    def analyze(self, results: list[ScrapeResult]) -> str:
        """スクレイピング結果をClaude APIに送信し、分析テキストを返す。"""

        # 取得できたデータのみを構造化してプロンプトに組み込む
        data_sections: list[str] = []
        for r in results:
            header = f"=== {r.source} ({r.url}) ==="
            if r.ok:
                data_sections.append(f"{header}\n{r.content}")
            else:
                reason = r.error or "データ取得失敗（内容が空）"
                data_sections.append(f"{header}\n※取得失敗: {reason}")

        user_message = (
            f"取得日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            + "\n\n".join(data_sections)
        )

        # 全サイト取得失敗時でも分析を試みる
        if not any(r.ok for r in results):
            user_message += (
                "\n\n※注意: 全てのデータソースで取得に失敗しました。"
                "取得可能な一般知識と直近の市場傾向から推測してください。"
                "ただし、リアルタイムデータなしのため確信度は「低」としてください。"
            )

        response = self._client.messages.create(
            model=MODEL_NAME,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text


# ---------------------------------------------------------------------------
# CLI Display
# ---------------------------------------------------------------------------


def display_scrape_summary(results: list[ScrapeResult]) -> None:
    """スクレイピング結果のサマリーを表示する。"""
    print(f"\n{BOLD}{CYAN}[データ取得結果]{RESET}")
    print(SEPARATOR)
    for r in results:
        status = f"{GREEN}OK{RESET}" if r.ok else f"{RED}NG{RESET}"
        chars = len(r.content) if r.ok else 0
        line = (
            f"  {status}  {r.source:<24s}  "
            f"{r.elapsed_sec:5.1f}s  {chars:>6,} chars"
        )
        print(line)
        if r.error:
            print(f"       {DIM}{r.error}{RESET}")
    print(SEPARATOR)

    ok_count = sum(1 for r in results if r.ok)
    print(
        f"  取得成功: {ok_count}/{len(results)} サイト\n"
    )


def display_analysis(analysis: str) -> None:
    """AI分析結果を色付きで表示する。"""
    print(f"{BOLD}{CYAN}[AI相場判定 - {MODEL_NAME}]{RESET}")
    print(SEPARATOR)
    colored = _color_verdict(analysis)
    print(colored)
    print(SEPARATOR)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run() -> None:
    """メインの非同期エントリポイント。"""
    print(f"\n{BOLD}{'=' * 72}{RESET}")
    print(f"{BOLD}{CYAN}  日経225先物ミニ AI相場判定システム{RESET}")
    print(f"{BOLD}{'=' * 72}{RESET}")
    print(f"  {DIM}実行時刻: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RESET}")
    print(f"  {DIM}モデル  : {MODEL_NAME}{RESET}\n")

    # --- Phase 1: 並列スクレイピング ---
    logger.info("スクレイピング開始 (3サイト並列)")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 720},
            locale="ja-JP",
        )

        results: list[ScrapeResult] = await asyncio.gather(
            Scraper.scrape_nikkei225jp(context),
            Scraper.scrape_kabutan(context),
            Scraper.scrape_investing(context),
        )

        await context.close()
        await browser.close()

    display_scrape_summary(results)

    # --- Phase 2: AI分析 ---
    logger.info("AI分析開始")

    try:
        analyzer = Analyzer(api_key=ANTHROPIC_API_KEY)
        analysis = analyzer.analyze(results)
        display_analysis(analysis)
    except ValueError as exc:
        print(f"\n{RED}設定エラー: {exc}{RESET}")
        print(f"{DIM}  .env ファイルに ANTHROPIC_API_KEY を設定してください。{RESET}\n")
        sys.exit(1)
    except anthropic.APIError as exc:
        print(f"\n{RED}API エラー: {exc}{RESET}\n")
        sys.exit(1)

    print(
        f"{DIM}  ※ 本ツールの出力は投資助言ではありません。"
        f"投資判断は自己責任でお願いします。{RESET}\n"
    )


def main() -> None:
    """同期エントリポイント。"""
    asyncio.run(run())


if __name__ == "__main__":
    main()
