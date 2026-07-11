"""Paper trading: virtual wallet with shares and EUR PnL."""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from funtrade.config import Settings, get_connection, read_sql_df


@dataclass
class PaperFill:
    id: int
    symbol: str
    side: str
    qty_shares: float
    price: float
    fee_eur: float
    cash_after: float


MIN_TRADE_EUR = 1.0


@dataclass
class PaperSettings:
    initial_cash: float
    position_limit_shares: float
    fee_bps: float
    trade_slice_pct: float
    csv_path: Path

    @classmethod
    def from_env(cls) -> PaperSettings:
        return cls(
            initial_cash=float(os.getenv("PAPER_INITIAL_CASH_EUR", "100000")),
            position_limit_shares=float(os.getenv("PAPER_POSITION_LIMIT_SHARES", "1000")),
            fee_bps=float(os.getenv("PAPER_FEE_BPS", "5")),
            trade_slice_pct=float(os.getenv("PAPER_TRADE_SLICE_PCT", "0.10")),
            csv_path=Path(os.getenv("PAPER_CSV_PATH", "data/paper_trades.csv")),
        )

    def slice_notional_eur(self) -> float:
        """Max EUR per tranche from start wallet (initial_cash), not current NAV."""
        return self.initial_cash * self.trade_slice_pct


def _fee_rate(fee_bps: float) -> float:
    return fee_bps / 10000.0


def _deviation_scale(epsilon: float | None, threshold: float | None) -> float:
    """Smaller slices when |ε| is extreme — mean-reversion target not reached yet."""
    if epsilon is None or threshold is None or threshold <= 0:
        return 1.0
    if abs(epsilon) <= threshold:
        return 1.0
    return min(1.0, threshold / abs(epsilon))


def compute_trade_qty(
    *,
    side: str,
    price: float,
    cash_eur: float,
    net_qty: float,
    paper: PaperSettings,
    epsilon: float | None = None,
    epsilon_threshold: float | None = None,
    qty_shares: float | None = None,
) -> float:
    """Fractional shares from a EUR slice (based on start wallet size).

    Buy slice = min(10% of initial cash, cash still available).
    Sell slice = 10% of initial cash in shares (partial exit), capped by position.
    """
    if price <= 0:
        return 0.0

    if qty_shares is not None:
        qty = qty_shares
        if side == "sell":
            qty = min(qty, net_qty)
        return max(qty, 0.0)

    scale = _deviation_scale(epsilon, epsilon_threshold)
    slice_eur = paper.slice_notional_eur() * scale

    if side == "buy":
        if cash_eur <= 0:
            return 0.0
        fee_mult = 1.0 + _fee_rate(paper.fee_bps)
        max_spend = min(slice_eur, cash_eur / fee_mult)
        if max_spend < MIN_TRADE_EUR:
            return 0.0
        qty = max_spend / price
        room = paper.position_limit_shares - net_qty
        if room <= 0:
            return 0.0
        return min(qty, room)

    if net_qty <= 0:
        return 0.0
    slice_qty = slice_eur / price
    return min(slice_qty, net_qty)


def _position_after_trade(
    net_qty: float,
    avg_price: float,
    side: str,
    qty: float,
    price: float,
) -> tuple[float, float, float]:
    delta = qty if side == "buy" else -qty
    new_qty = net_qty + delta
    if abs(net_qty) < 1e-12:
        return new_qty, price, 0.0

    same_direction = (net_qty > 0 and delta > 0) or (net_qty < 0 and delta < 0)
    if same_direction:
        total = abs(net_qty) * avg_price + qty * price
        return new_qty, total / abs(new_qty), 0.0

    close_qty = min(abs(net_qty), qty)
    if net_qty > 0:
        realized = (price - avg_price) * close_qty
    else:
        realized = (avg_price - price) * close_qty

    if abs(new_qty) < 1e-12:
        return 0.0, 0.0, realized

    if (new_qty > 0) != (net_qty > 0):
        return new_qty, price, realized

    return new_qty, avg_price, realized


def _ensure_portfolio(cur, paper: PaperSettings) -> None:
    cur.execute("SELECT id FROM paper_portfolio WHERE id = 1")
    if cur.fetchone() is None:
        cur.execute(
            "INSERT INTO paper_portfolio (id, cash_eur, realized_pnl) VALUES (1, %s, 0)",
            (paper.initial_cash,),
        )


def _append_csv(fill: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_header = not path.exists()
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "executed_at",
                "symbol",
                "side",
                "qty_shares",
                "price",
                "fee_eur",
                "epsilon",
                "regime_valid",
                "signal",
            ],
        )
        if write_header:
            writer.writeheader()
        writer.writerow(fill)


def execute_trade(
    signal: int,
    symbol: str,
    price: float,
    *,
    epsilon: float | None = None,
    epsilon_threshold: float | None = None,
    regime_valid: bool = True,
    qty_shares: float | None = None,
    paper: PaperSettings | None = None,
    settings: Settings | None = None,
) -> PaperFill | None:
    if signal == 0 or not regime_valid:
        return None

    paper = paper or PaperSettings.from_env()
    settings = settings or Settings.from_env()
    side = "buy" if signal > 0 else "sell"
    now = datetime.now(UTC)

    with get_connection(settings) as conn:
        with conn.cursor() as cur:
            _ensure_portfolio(cur, paper)

            cur.execute("SELECT cash_eur FROM paper_portfolio WHERE id = 1")
            cash_row = cur.fetchone()
            cash_eur = float(cash_row[0]) if cash_row else 0.0

            cur.execute(
                "SELECT net_qty_shares, avg_price FROM paper_positions WHERE symbol = %s",
                (symbol,),
            )
            row = cur.fetchone()
            net_qty = float(row[0]) if row else 0.0
            avg_price = float(row[1]) if row and row[1] is not None else price

            if signal < 0 and net_qty <= 0:
                return None

            qty = compute_trade_qty(
                side=side,
                price=price,
                cash_eur=cash_eur,
                net_qty=net_qty,
                paper=paper,
                epsilon=epsilon,
                epsilon_threshold=epsilon_threshold,
                qty_shares=qty_shares,
            )
            if qty <= 0:
                return None

            notional = price * qty
            fee = notional * _fee_rate(paper.fee_bps)
            if side == "buy" and notional + fee > cash_eur + 1e-9:
                return None
            if notional < MIN_TRADE_EUR:
                return None

            new_qty, new_avg, realized_delta = _position_after_trade(
                net_qty, avg_price, side, qty, price
            )

            if new_qty > paper.position_limit_shares + 1e-9:
                return None

            cur.execute(
                """
                INSERT INTO paper_trades
                    (executed_at, symbol, side, qty_shares, price, fee_eur,
                     epsilon, regime_valid, signal)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (now, symbol, side, qty, price, fee, epsilon, regime_valid, signal),
            )
            trade_id = cur.fetchone()[0]

            cash_delta = -notional - fee if side == "buy" else notional - fee
            cur.execute(
                """
                UPDATE paper_portfolio
                SET cash_eur = cash_eur + %s,
                    realized_pnl = realized_pnl + %s,
                    updated_at = %s
                WHERE id = 1
                RETURNING cash_eur
                """,
                (cash_delta, realized_delta, now),
            )
            cash_after = float(cur.fetchone()[0])

            if abs(new_qty) < 1e-12:
                cur.execute("DELETE FROM paper_positions WHERE symbol = %s", (symbol,))
            else:
                cur.execute(
                    """
                    INSERT INTO paper_positions (symbol, net_qty_shares, avg_price, updated_at)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (symbol) DO UPDATE SET
                      net_qty_shares = EXCLUDED.net_qty_shares,
                      avg_price = EXCLUDED.avg_price,
                      updated_at = EXCLUDED.updated_at
                    """,
                    (symbol, new_qty, new_avg, now),
                )

        conn.commit()

    fill_dict = {
        "executed_at": now.isoformat(),
        "symbol": symbol,
        "side": side,
        "qty_shares": qty,
        "price": price,
        "fee_eur": fee,
        "epsilon": epsilon,
        "regime_valid": regime_valid,
        "signal": signal,
    }
    _append_csv(fill_dict, paper.csv_path)

    return PaperFill(
        id=trade_id,
        symbol=symbol,
        side=side,
        qty_shares=qty,
        price=price,
        fee_eur=fee,
        cash_after=cash_after,
    )


def get_position_quantities(
    settings: Settings | None = None,
) -> dict[str, float]:
    """Net shares per symbol — lightweight lookup for recommendations (no mark prices)."""
    settings = settings or Settings.from_env()
    with get_connection(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT symbol, net_qty_shares
                FROM paper_positions
                WHERE ABS(net_qty_shares) > 1e-12
                ORDER BY symbol
                """
            )
            rows = cur.fetchall()
    return {str(symbol): float(qty) for symbol, qty in rows}


def get_portfolio_summary(
    settings: Settings | None = None,
    paper: PaperSettings | None = None,
) -> dict:
    from funtrade.data.market import latest_price

    settings = settings or Settings.from_env()
    paper = paper or PaperSettings.from_env()
    with get_connection(settings) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT cash_eur, realized_pnl, updated_at FROM paper_portfolio WHERE id = 1"
            )
            port = cur.fetchone()
            cur.execute(
                "SELECT symbol, net_qty_shares, avg_price FROM paper_positions ORDER BY symbol"
            )
            positions = cur.fetchall()

    if port is None:
        return {
            "cash_eur": 0.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "total_pnl": 0.0,
            "initial_cash_eur": paper.initial_cash,
            "positions": [],
        }

    cash = float(port[0])
    realized = float(port[1])
    unrealized = 0.0
    enriched: list[dict] = []
    for symbol, qty_raw, avg_raw in positions:
        qty = float(qty_raw)
        if abs(qty) < 1e-12:
            continue
        avg = float(avg_raw) if avg_raw is not None else 0.0
        mark = latest_price(symbol, settings=settings)
        pos_unrealized = qty * (mark - avg) if mark is not None else None
        if pos_unrealized is not None:
            unrealized += pos_unrealized
        enriched.append(
            {
                "symbol": symbol,
                "net_qty_shares": qty,
                "avg_price": avg,
                "mark_price": mark,
                "unrealized_pnl_eur": pos_unrealized,
            }
        )

    return {
        "cash_eur": cash,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "total_pnl": realized + unrealized,
        "initial_cash_eur": paper.initial_cash,
        "updated_at": port[2].isoformat() if port[2] else None,
        "positions": enriched,
    }


def load_recent_trades(limit: int = 50, settings: Settings | None = None):
    settings = settings or Settings.from_env()
    return read_sql_df(
        """
        SELECT executed_at, symbol, side, qty_shares, price, fee_eur,
               epsilon, regime_valid, signal
        FROM paper_trades
        ORDER BY executed_at DESC
        LIMIT %(limit)s
        """,
        {"limit": limit},
        settings=settings,
    )


def reset_paper_portfolio(
    paper: PaperSettings | None = None,
    settings: Settings | None = None,
) -> None:
    paper = paper or PaperSettings.from_env()
    settings = settings or Settings.from_env()
    with get_connection(settings) as conn:
        with conn.cursor() as cur:
            _ensure_portfolio(cur, paper)
            cur.execute("TRUNCATE paper_trades RESTART IDENTITY")
            cur.execute("DELETE FROM paper_positions")
            cur.execute(
                """
                UPDATE paper_portfolio
                SET cash_eur = %s, realized_pnl = 0, updated_at = NOW()
                WHERE id = 1
                """,
                (paper.initial_cash,),
            )
        conn.commit()
    if paper.csv_path.exists():
        paper.csv_path.unlink()
