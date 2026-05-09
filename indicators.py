import pandas as pd
import ta
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from hyperliquid.info import Info
from hyperliquid.utils import constants

# Costante Fee Taker standard Hyperliquid (0.035%)
TAKER_FEE_RATE = 0.00035

INTERVAL_TO_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}

class CryptoTechnicalAnalysisHL:
    """
    Analisi tecnica usando l'API Info di Hyperliquid.
    Tutti gli indicatori principali sono centrati sul timeframe 15 minuti.
    """

    def __init__(self, testnet: bool = True):
        # Se vuoi i prezzi veri usa testnet=False
        base_url = constants.TESTNET_API_URL if testnet else constants.MAINNET_API_URL
        self.info = Info(base_url, skip_ws=True)
        # Cache per i metadati globali (Funding, OI, Mark Price)
        self._market_state_cache = None
        self._market_state_timestamp = 0

    # ==============================
    #       HELPER MARKET STATE
    # ==============================
    def _get_global_state(self):
        """Recupera meta_and_asset_ctxs con una mini-cache per efficienza."""
        now = datetime.now().timestamp()
        if self._market_state_cache and (now - self._market_state_timestamp < 2):
            return self._market_state_cache
        
        try:
            self._market_state_cache = self.info.meta_and_asset_ctxs()
            self._market_state_timestamp = now
            return self._market_state_cache
        except Exception as e:
            print(f"Warning: Impossibile recuperare stato globale: {e}")
            return None

    def get_market_details(self, coin: str) -> Dict[str, float]:
        """
        Estrae dati reali (Funding, OI, Mark Price) per il coin specificato.
        """
        state = self._get_global_state()
        if not state:
            return {"funding": 0.0, "oi": 0.0, "mark_px": 0.0}

        universe_dict, contexts_list = state
        
        # Cerca indice del coin
        coin_index = -1
        try:
            for idx, asset in enumerate(universe_dict["universe"]):
                if asset["name"] == coin:
                    coin_index = idx
                    break
        except Exception:
            return {"funding": 0.0, "oi": 0.0, "mark_px": 0.0}

        if coin_index == -1 or coin_index >= len(contexts_list):
            return {"funding": 0.0, "oi": 0.0, "mark_px": 0.0}

        ctx = contexts_list[coin_index]
        return {
            "funding": float(ctx.get("funding", 0.0)),
            "oi": float(ctx.get("openInterest", 0.0)),
            "mark_px": float(ctx.get("markPx", 0.0))
        }

    # ==============================
    #       FETCH OHLCV (HL)
    # ==============================

    def get_orderbook_volume(self, ticker: str) -> str:
        """
        Restituisce una stringa con i volumi totali di bid e ask per un ticker.
        """
        coin = ticker.split('-')[0].upper()

        try:
            orderbook = self.info.l2_snapshot(coin)
        except Exception as e:
            return f"Errore recuperando orderbook: {e}"

        if not orderbook or "levels" not in orderbook:
            return f"Nessun dato disponibile per {coin}"

        bids = orderbook["levels"][0]
        asks = orderbook["levels"][1]

        bid_volume = sum(float(level["sz"]) for level in bids)
        ask_volume = sum(float(level["sz"]) for level in asks)

        return f"Bid Vol: {bid_volume:.2f}, Ask Vol: {ask_volume:.2f}"

    def fetch_ohlcv(self, coin: str, interval: str, limit: int = 500) -> pd.DataFrame:
        """
        Recupera i dati OHLCV da Hyperliquid tramite Info.candles_snapshot.
        """
        if interval not in INTERVAL_TO_MS:
            raise ValueError(f"Interval '{interval}' non supportato in INTERVAL_TO_MS")

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        step_ms = INTERVAL_TO_MS[interval]
        start_ms = now_ms - limit * step_ms

        ohlcv_data = self.info.candles_snapshot(
            name=coin,
            interval=interval,
            startTime=start_ms,
            endTime=now_ms,
        )

        if not ohlcv_data:
            raise RuntimeError(f"Nessuna candela ricevuta per {coin} ({interval})")

        df = pd.DataFrame(ohlcv_data)

        df["timestamp"] = pd.to_datetime(df["t"], unit="ms", utc=True)

        df = df[["timestamp", "o", "h", "l", "c", "v"]].copy()
        df.rename(
            columns={
                "o": "open",
                "h": "high",
                "l": "low",
                "c": "close",
                "v": "volume",
            },
            inplace=True,
        )

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    # ==============================
    #       INDICATORI TECNICI
    # ==============================
    def calculate_ema(self, data: pd.Series, period: int) -> pd.Series:
        return ta.trend.EMAIndicator(data, window=period).ema_indicator()

    def calculate_macd(self, data: pd.Series) -> Tuple[pd.Series, pd.Series, pd.Series]:
        macd = ta.trend.MACD(data)
        return macd.macd(), macd.macd_signal(), macd.macd_diff()

    def calculate_rsi(self, data: pd.Series, period: int) -> pd.Series:
        return ta.momentum.RSIIndicator(data, window=period).rsi()

    def calculate_atr(
        self, high: pd.Series, low: pd.Series, close: pd.Series, period: int
    ) -> pd.Series:
        return ta.volatility.AverageTrueRange(
            high, low, close, window=period
        ).average_true_range()

    def calculate_pivot_points(
        self, high: float, low: float, close: float
    ) -> Dict[str, float]:
        pp = (high + low + close) / 3.0
        s1 = (2 * pp) - high
        s2 = pp - (high - low)
        r1 = (2 * pp) - low
        r2 = pp + (high - low)
        return {"pp": pp, "s1": s1, "s2": s2, "r1": r1, "r2": r2}

    # ==============================
    #       ANALISI COMPLETA A 15m
    # ==============================
    def get_complete_analysis(self, ticker: str) -> Dict:
        coin = ticker.upper()

        # 1) DATI 15 MINUTI (intraday principale)
        df_15m = self.fetch_ohlcv(coin, "15m", limit=200)

        df_15m["ema_20"] = self.calculate_ema(df_15m["close"], 20)
        macd_line, signal_line, macd_diff = self.calculate_macd(df_15m["close"])
        df_15m["macd"] = macd_diff
        df_15m["rsi_7"] = self.calculate_rsi(df_15m["close"], 7)
        df_15m["rsi_14"] = self.calculate_rsi(df_15m["close"], 14)

        last_10_15m = df_15m.tail(10)

        # 2) CONTESTO "longer term" sempre a 15m ma su finestra più lunga
        longer_term = df_15m.tail(50).copy()
        longer_term["ema_20"] = self.calculate_ema(longer_term["close"], 20)
        longer_term["ema_50"] = self.calculate_ema(longer_term["close"], 50)
        longer_term["atr_3"] = self.calculate_atr(
            longer_term["high"], longer_term["low"], longer_term["close"], 3
        )
        longer_term["atr_14"] = self.calculate_atr(
            longer_term["high"], longer_term["low"], longer_term["close"], 14
        )
        macd_15m_long, _, macd_diff_15m_long = self.calculate_macd(
            longer_term["close"]
        )
        longer_term["macd"] = macd_diff_15m_long
        longer_term["rsi_14"] = self.calculate_rsi(longer_term["close"], 14)

        avg_volume = longer_term["volume"].tail(20).mean()
        last_10_longer = longer_term.tail(10)

        # 3) PIVOT POINTS daily
        df_daily = self.fetch_ohlcv(coin, "1d", limit=2)
        if len(df_daily) >= 2:
            prev_day = df_daily.iloc[-2]
            pivot_points = self.calculate_pivot_points(
                prev_day["high"], prev_day["low"], prev_day["close"]
            )
        else:
            last = df_15m.iloc[-1]
            pivot_points = self.calculate_pivot_points(
                last["high"], last["low"], last["close"]
            )

        # --- MODIFICA: Recupero dati reali Market State ---
        mkt_details = self.get_market_details(coin)
        funding_rate = mkt_details["funding"]
        oi_latest = mkt_details["oi"]
        mark_px = mkt_details["mark_px"]

        # Se il mark price è 0 (errore), usa l'ultimo close
        if mark_px == 0:
            mark_px = df_15m.iloc[-1]["close"]

        # Calcolo Fee Stimata (Prezzo Transazione)
        estimated_fee = mark_px * TAKER_FEE_RATE
        # --------------------------------------------------

        current_15m = df_15m.iloc[-1]
        current_longer = longer_term.iloc[-1]

        result = {
            "ticker": ticker,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            
            "current": {
                "price": current_15m["close"],
                "ema20": current_15m["ema_20"],
                "macd": current_15m["macd"],
                "rsi_7": current_15m["rsi_7"],
            },
            "volume": self.get_orderbook_volume(ticker),
            "pivot_points": pivot_points,

            "derivatives": {
                "open_interest_latest": oi_latest,
                # Average OI non è disponibile storicamente via API semplice, metto latest
                "open_interest_average": oi_latest, 
                "funding_rate": funding_rate,
                "estimated_fee_cost": estimated_fee, # NUOVO CAMPO
            },

            "intraday": {
                "mid_prices": last_10_15m["close"].tolist(),
                "ema_20": last_10_15m["ema_20"].tolist(),
                "macd": last_10_15m["macd"].tolist(),
                "rsi_7": last_10_15m["rsi_7"].tolist(),
                "rsi_14": last_10_15m["rsi_14"].tolist(),
            },

            "longer_term_15m": {
                "ema_20_current": current_longer["ema_20"],
                "ema_50_current": current_longer["ema_50"],
                "atr_3_current": current_longer["atr_3"],
                "atr_14_current": current_longer["atr_14"],
                "volume_current": current_longer["volume"],
                "volume_average": avg_volume,
                "macd_series": last_10_longer["macd"].tolist(),
                "rsi_14_series": last_10_longer["rsi_14"].tolist(),
            },
        }
        return result

    def format_output(self, data: Dict) -> str:
        output = f"\n<{data['ticker']}_data>\n"
        output += f"Timestamp: {data['timestamp']} (UTC) (Hyperliquid, 15m)\n"
        output += f"\n"

        curr = data["current"]
        output += (
            f"current_price = {curr['price']:.1f}, "
            f"current_ema20 = {curr['ema20']:.3f}, "
            f"current_macd = {curr['macd']:.3f}, "
            f"current_rsi (7 period) = {curr['rsi_7']:.3f}\n\n"
        )
        output += f"Volume: {data['volume']}\n\n"

        pivot = data["pivot_points"]
        output += "Pivot Points (based on previous day):\n"
        output += (
            f"R2 = {pivot['r2']:.2f}, R1 = {pivot['r1']:.2f}, "
            f"PP = {pivot['pp']:.2f}, "
            f"S1 = {pivot['s1']:.2f}, S2 = {pivot['s2']:.2f}\n\n"
        )

        deriv = data["derivatives"]
        output += (
            f"In addition, here is the latest {data['ticker']} funding data on Hyperliquid:\n"
        )
        # Qui ho aggiornato per mostrare i dati reali e la FEE
        output += (
            f"Open Interest: Latest: {deriv['open_interest_latest']:.2f}\n"
        )
        output += f"Funding Rate: {deriv['funding_rate']:.6f}\n"
        # NUOVA RIGA RICHIESTA: Costo Transazione
        output += f"Est. Transaction Fee (0.035%): {deriv['estimated_fee_cost']:.4f} USD\n\n"

        intra = data["intraday"]
        output += "Intraday series (15m, oldest → latest):\n"
        output += (
            f"Mid prices: {[round(x, 1) for x in intra['mid_prices']]}\n"
            f"EMA indicators (20-period): {[round(x, 3) for x in intra['ema_20']]}\n"
            f"MACD indicators: {[round(x, 3) for x in intra['macd']]}\n"
            f"RSI indicators (7-Period): {[round(x, 3) for x in intra['rsi_7']]}\n"
            f"RSI indicators (14-Period): {[round(x, 3) for x in intra['rsi_14']]}\n\n"
        )

        lt = data["longer_term_15m"]
        output += "Longer-term context (still 15-minute timeframe, wider window):\n"
        output += (
            f"20-Period EMA: {lt['ema_20_current']:.3f} vs. "
            f"50-Period EMA: {lt['ema_50_current']:.3f}\n"
            f"3-Period ATR: {lt['atr_3_current']:.3f} vs. "
            f"14-Period ATR: {lt['atr_14_current']:.3f}\n"
            f"Current Volume: {lt['volume_current']:.3f} vs. "
            f"Average Volume: {lt['volume_average']:.3f}\n"
            f"MACD indicators: {[round(x, 3) for x in lt['macd_series']]}\n"
            f"RSI indicators (14-Period): {[round(x, 3) for x in lt['rsi_14_series']]}\n"
        )
        output += f"<{data['ticker']}_data>\n"
        return output


def analyze_multiple_tickers(tickers: List[str], testnet: bool = True) -> Tuple[str, List[Dict]]:
    analyzer = CryptoTechnicalAnalysisHL(testnet=testnet)
    full_output = ""
    datas = []
    data = None
    for ticker in tickers:
        try:
            data = analyzer.get_complete_analysis(ticker)
            datas.append(data)
            full_output += analyzer.format_output(data)
        except Exception as e:
            print(f"Errore durante l'analisi di {ticker}: {e}")
            full_output += f"\nError analyzing {ticker}: {e}\n"
    return full_output, datas


# if __name__ == "__main__":
#     # Esempio uso: testnet=False per dati reali
#     tickers = ["BTC", "ETH", "SOL"]
#     result_str, result_data = analyze_multiple_tickers(tickers, testnet=False)
#     print(result_str)