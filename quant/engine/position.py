"""RQAlpha 股票持仓扩展：登记日权益、实际到账/可卖日与持有批次税款。

只扩展选定引擎的公司行动/终止事件；订单、成交、现金及持仓队列仍由 RQAlpha 维护。
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

import pandas as pd
from rqalpha.const import INSTRUMENT_TYPE, POSITION_DIRECTION, POSITION_EFFECT, TAX_TYPE
from rqalpha.core.events import EVENT, Event
from rqalpha.mod.rqalpha_mod_sys_accounts.position_model import StockPosition
from rqalpha.portfolio.position import Position, PositionQueue


class _StockLot(NamedTuple):
    buy_date: date
    original_code: str


class _ExchangeReceivable(NamedTuple):
    pay_date: date | None
    amount: float
    event_id: str
    source_ref: str
    cash_exit_lots: tuple[tuple[_StockLot, int], ...]


@dataclass
class _RegisteredLot:
    lot: _StockLot
    registered_quantity: int
    untaxed_quantity: float
    untaxed_dividend: float
    tax_lot: _StockLot | None = None

    def evidence(self):
        result = dict(original_code=self.lot.original_code, buy_date=self.lot.buy_date.isoformat(),
                      registered_quantity=self.registered_quantity, untaxed_quantity=self.untaxed_quantity)
        if self.tax_lot is not None and self.tax_lot != self.lot:
            result["tax_lot"] = dict(original_code=self.tax_lot.original_code,
                                     buy_date=self.tax_lot.buy_date.isoformat())
        return result


class _StockLotQueue(PositionQueue):
    """Keep RQAlpha's FIFO quantities with the originating security on each acquisition lot."""

    def __init__(self, code):
        super().__init__()
        self.code = code

    def set_sate(self, state):
        super().set_sate([(key if isinstance(key, _StockLot) else _StockLot(key, self.code), qty)
                          for key, qty in state])

    def handle_trade(self, delta_quantity, trading_date, close_today=False):
        return super().handle_trade(delta_quantity, _StockLot(trading_date, self.code), close_today)


class HetuStockPosition(StockPosition):
    __instrument_types__ = (INSTRUMENT_TYPE.CS,)
    rules = None
    action_events = {}
    restrictions = []
    terminal_disposals = {}
    disposition_events = []
    cash_dividend_events = []
    exchange_cash_events = []
    cash_exit_tax_events = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        initial_lots = self._queue.queue
        self._queue = _StockLotQueue(self.order_book_id)
        self._queue.set_sate(initial_lots)
        self._rights = {}
        self._applied = set()
        self._bonus_locked = []
        self._tax_records = []
        self._exchange_receivables = []
        self._exchange_sale_locks = []
        self._exchange_frozen = False

    @property
    def position_queue(self):
        return deque((lot.buy_date, qty) for lot, qty in self._queue.queue)

    def rename(self, instrument):
        # 权益登记键保留原代码，持仓队列、成本和已登记权益仍归同一账户。
        self._instrument = instrument
        self._order_book_id = instrument.order_book_id
        self._queue.code = self._order_book_id

    def __bool__(self):
        return super().__bool__() or bool(self._rights or self._exchange_receivables) or self.dividend_tax_payable > 0

    def exchange(self, instrument, event, trading_date):
        ratio = event["ratio"]
        before = self.quantity
        exact = before * ratio
        qty = int(exact)
        fraction = exact - qty
        # Whole lots use cumulative floors so aggregate and acquisition dates stay consistent.
        lots, conversion, remainder_lots, total, assigned = [], {}, [], 0, 0
        for lot, held_qty in self._queue.queue:
            total += held_qty
            next_assigned = int(total * ratio)
            if next_assigned > assigned:
                new_qty = next_assigned - assigned
                lots.append((lot, new_qty))
                conversion[lot] = lot, new_qty / held_qty
                # Earlier sub-share lots participate in this whole-share lot's settlement.
                conversion.update((earlier, (lot, new_qty / quantity)) for earlier, quantity in remainder_lots)
                remainder_lots.clear()
            else:
                remainder_lots.append((lot, held_qty))
            assigned = next_assigned
        if lots:
            last_lot, last_qty = lots[-1]
            conversion.update((later, (last_lot, last_qty / quantity)) for later, quantity in remainder_lots)
        cash_exit_lots = tuple(self._queue.queue) if not qty else ()
        self._queue.set_sate(lots)
        self._old_quantity = int(self._old_quantity * ratio)
        self._logical_old_quantity = int(self._logical_old_quantity * ratio)
        self._quantity = qty
        self._avg_price /= float(ratio)
        self._bonus_locked = [(day, int(n * ratio)) for day, n in self._bonus_locked]
        for key, rights in self._rights.items():
            if self.action_events[key[0]][key[1]]["action_type"] == "cash_dividend":
                for right in rights:
                    # Change the units that can be sold, while retaining the original registered dividend.
                    target = conversion.get(right.tax_lot or right.lot)
                    if target is not None:
                        right.tax_lot, credit_ratio = target
                        right.untaxed_quantity *= credit_ratio
        for record in self._tax_records:
            target = conversion.get(record["tax_lot"])
            if target is not None:
                record["tax_lot"], credit_ratio = target
                record["quantity"] *= credit_ratio
                record["remaining"] *= credit_ratio
        self.rename(instrument)
        release = event.get("sellable_date")
        self._exchange_frozen = False
        self._exchange_sale_locks = [(date.fromisoformat(release) if release else date.max, lots)]

        fraction_cash, pay_day = event.get("fraction_cash_cny"), event.get("fraction_pay_date")
        amount = None
        if fraction and event.get("fraction_rule") == "cash_in_lieu" and fraction_cash is not None:
            amount = float(fraction_cash * fraction.numerator / fraction.denominator)
            payment = date.fromisoformat(pay_day) if pay_day else None
            source_ref = f"bundle:evidence/{event['evidence_ref']}"
            self._exchange_receivables.append(
                _ExchangeReceivable(payment, amount, event["event_id"], source_ref, cash_exit_lots)
            )
            self.exchange_cash_events.append({
                "code": self.order_book_id, "date": trading_date.isoformat(), "kind": "exchange_fraction_accrual",
                "amount_cny": amount, "source_ref": source_ref,
                "event_id": event["event_id"], "pay_date": pay_day,
            })
        return before, qty, str(fraction), amount

    @property
    def exchange_receivable(self):
        return sum(receivable.amount for receivable in self._exchange_receivables)

    def freeze_exchange(self):
        self._exchange_frozen = True

    def absorb_exchange(self, converted):
        """Merge the converted lots and claims into the independently held successor."""
        cost = self.quantity * self.avg_price + converted.quantity * converted.avg_price
        self._quantity += converted.quantity
        self._old_quantity += converted._old_quantity
        self._logical_old_quantity += converted._logical_old_quantity
        self._avg_price = cost / self._quantity if self._quantity else 0.0
        self._queue.set_sate(sorted([*self._queue.queue, *converted._queue.queue]))
        for key, lots in converted._rights.items():
            self._rights.setdefault(key, []).extend(lots)
        self._applied.update(converted._applied)
        self._tax_records.extend(converted._tax_records)
        self._bonus_locked.extend(converted._bonus_locked)
        self._exchange_sale_locks.extend(converted._exchange_sale_locks)
        self._exchange_receivables.extend(converted._exchange_receivables)
        self._dividend_receivable = deque(sorted([*self._dividend_receivable, *converted._dividend_receivable]))

    def _locked_exchange_lots(self):
        locked = defaultdict(int)
        today = self._env.trading_dt.date()
        for release, lots in self._exchange_sale_locks:
            if release > today:
                for lot, quantity in lots:
                    locked[lot] += quantity
        return locked

    def apply_trade(self, trade):
        locks = self._locked_exchange_lots()
        if trade.position_effect != POSITION_EFFECT.CLOSE or not locks:
            return super().apply_trade(trade)
        # RQAlpha sells oldest available lots first. Preserve the acquisition dates of locked conversion lots.
        available, protected = [], []
        for lot, quantity in self._queue.queue:
            reserved = min(locks[lot], quantity)
            locks[lot] -= reserved
            if reserved:
                protected.append((lot, reserved))
            if quantity > reserved:
                available.append((lot, quantity - reserved))
        self._queue.set_sate(available)
        try:
            return super().apply_trade(trade)
        finally:
            self._queue.set_sate(sorted([*self._queue.queue, *protected]))

    def _after_position_queue_updated(self, trade, close_details):
        if trade.position_effect == POSITION_EFFECT.CLOSE:
            self._pay_dividend_tax(trade.trading_datetime, close_details, trade_id=str(trade.exec_id))

    def _tax_rate(self, buy_date: date, day: date) -> float:
        components = self.rules.fee_components(self.order_book_id, day.isoformat())
        if components["dividend_mode"] == "zero":
            return 0.0
        # 已批准的2015-09-08起境内个人差别化税制；其他历史口径明确拒绝。
        if day < date(2015, 9, 8):
            raise ValueError("holding_diff: 2015-09-08之前税制未在本路线核验")
        one_month = (pd.Timestamp(day) - pd.DateOffset(months=1)).date()
        one_year = (pd.Timestamp(day) - pd.DateOffset(years=1)).date()
        return .2 if buy_date >= one_month else (.1 if buy_date >= one_year else 0.0)

    @property
    def dividend_tax_payable(self):
        day = self._env.trading_dt.date()
        return sum(r["dividend"] * r["remaining"] / r["quantity"] * self._tax_rate(r["lot"].buy_date, day)
                   for r in self._tax_records if r["quantity"] > 0)

    @property
    def equity(self):
        return super().equity - self.dividend_tax_payable + self.exchange_receivable

    @property
    def closable(self):
        locked = sum(q for release, q in self._bonus_locked if release > self._env.trading_dt.date())
        if self._exchange_frozen:
            return 0
        locked += sum(self._locked_exchange_lots().values())
        return max(0, super().closable - locked)

    def before_trading(self, trading_date):
        delta_cash = Position.before_trading(self, trading_date)
        self._unadjusted_prev_close = self.last_price
        self._daily_dividend = 0.0
        self._daily_split = 1.0
        self._bonus_locked = [(d, q) for d, q in self._bonus_locked if d > trading_date]
        if self.direction != POSITION_DIRECTION.LONG:
            return delta_cash
        code = self.order_book_id
        keys = set(self._rights) | {(code, i) for i in range(len(self.action_events.get(code, ())))}
        due = [(key, self.action_events[key[0]][key[1]]) for key in sorted(keys - self._applied)
               if self.action_events[key[0]][key[1]]["ex_date"] == trading_date]
        for key, event in sorted(due, key=lambda pair: pair[1]["action_type"] != "cash_dividend"):
            action_code = key[0]
            rights = self._rights.pop(key, [])
            entitled = sum(right.registered_quantity for right in rights)
            if event["action_type"] == "cash_dividend":
                amount = entitled * event["d"]
                self._dividend_receivable.append((event["pay_date"], amount))
                if amount:
                    self.cash_dividend_events.append({
                        "code": str(self.order_book_id), "date": trading_date.isoformat(), "record_code": action_code,
                        "kind": "cash_dividend", "amount_cny": float(amount),
                        "entitled_qty": int(entitled), "cash_per_share": float(event["d"]),
                        "record_date": event["record_date"].isoformat(), "pay_date": event["pay_date"].isoformat(),
                        "entitlement_lots": [right.evidence() for right in rights],
                        "source_ref": "bundle:tables/corporate_actions.parquet",
                    })
                self._daily_dividend += amount
                for right in rights:
                    if right.untaxed_quantity > 0:
                        self._tax_records.append({"lot": right.lot, "tax_lot": right.tax_lot or right.lot,
                                                  "quantity": right.untaxed_quantity,
                                                  "remaining": right.untaxed_quantity,
                                                  "dividend": right.untaxed_dividend})
                if self._quantity:
                    self._avg_price -= amount / self._quantity
                    self._last_price -= amount / self._quantity
            elif event["action_type"] == "bonus_split" and entitled:
                lots = [(right.lot, right.registered_quantity) for right in rights]
                bonus = int((Decimal(entitled) * Decimal(str(event["m"] - 1))).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP))
                old_qty = self._quantity
                self._quantity += bonus
                self._old_quantity += bonus
                # 由引擎持仓队列保留原批次日期；登记后买入的批次不参加送转。
                queue = dict(self._queue.queue)
                remaining = bonus
                for n, (lot, qty) in enumerate(lots):
                    addition = remaining if n == len(lots) - 1 else round(qty * (event["m"] - 1))
                    queue[lot] = queue.get(lot, 0) + addition
                    remaining -= addition
                self._queue.set_sate(sorted(queue.items()))
                for record in self._tax_records:
                    if any(lot == record["tax_lot"] for lot, _ in lots):
                        record["quantity"] *= event["m"]
                        record["remaining"] *= event["m"]
                if self._quantity:
                    self._avg_price *= old_qty / self._quantity
                    self._last_price *= old_qty / self._quantity
                if bonus > 0:
                    self._bonus_locked.append((event["stock_listing_date"], bonus))
                self._daily_split = event["m"]
            self._applied.add(key)
        delta_cash += self._handle_dividend_payable(trading_date)
        outstanding = []
        for receivable in self._exchange_receivables:
            if receivable.pay_date is not None and receivable.pay_date <= trading_date:
                delta_cash += receivable.amount
                # Only a fully cash-settled conversion ends the holding of these lots here.
                if receivable.cash_exit_lots:
                    delta_cash -= self._pay_dividend_tax(
                        self._env.trading_dt, receivable.cash_exit_lots,
                        cash_settlement=dict(event_id=receivable.event_id, source_ref=receivable.source_ref,
                                             amount_cny=receivable.amount),
                    )
            else:
                outstanding.append(receivable)
        self._exchange_receivables = outstanding
        disposal = self.terminal_disposals.get((self.order_book_id, trading_date.isoformat()))
        if disposal is not None and self.quantity:
            amount = self.quantity * float(disposal["cash_per_share"])
            delta_cash += amount - self._pay_dividend_tax(
                self._env.trading_dt, self._queue.queue,
                cash_settlement=dict(event_id=disposal["event_id"], amount_cny=amount,
                                     source_ref=f"bundle:evidence/{disposal['evidence_ref']}"),
            )
            self.disposition_events.append(dict(disposal, quantity=self.quantity, cash_amount=amount))
            self._quantity = self._old_quantity = 0
            self._queue.clear()
        return delta_cash

    def settlement(self, trading_date):
        # 不调用StockPosition默认退市清零/末价返现；仅在明确处置证据路径处理。
        Position.settlement(self, trading_date)
        if self.direction != POSITION_DIRECTION.LONG:
            return 0.0
        for index, event in enumerate(self.action_events.get(self.order_book_id, [])):
            if event["record_date"] == trading_date:
                dividend = event["d"] if event["action_type"] == "cash_dividend" else 0
                self._rights[(self.order_book_id, index)] = [
                    _RegisteredLot(lot, qty, qty, qty * dividend) for lot, qty in self._queue.queue
                ]
        if self.quantity and self._instrument.de_listed_at(pd.Timestamp(trading_date)):
            limitation = (trading_date.isoformat(), self.order_book_id, "delisting_disposal_unproven")
            if limitation not in self.restrictions:
                self.restrictions.append(limitation)
        return 0.0

    def _pay_dividend_tax(self, trading_dt, close_details, *, trade_id=None, cash_settlement=None):
        tax = 0.0
        for closed_lot, closed_qty in close_details:
            # 登记后、除息前已转让原股：权益仍归原登记批次，税款按真实转让日扣缴。
            for key, rights in self._rights.items():
                code, index = key
                event = self.action_events[code][index]
                if event["action_type"] != "cash_dividend":
                    continue
                for right in rights:
                    if (right.tax_lot or right.lot) != closed_lot or right.untaxed_quantity <= 0:
                        continue
                    quantity = min(abs(closed_qty), right.untaxed_quantity)
                    dividend = right.untaxed_dividend * quantity / right.untaxed_quantity
                    tax += dividend * self._tax_rate(right.lot.buy_date, trading_dt.date())
                    right.untaxed_quantity -= quantity
                    right.untaxed_dividend -= dividend
            for record in self._tax_records:
                if record["tax_lot"] != closed_lot:
                    continue
                quantity = min(abs(closed_qty), record["remaining"])
                tax += quantity / record["quantity"] * record["dividend"] * self._tax_rate(
                    record["lot"].buy_date, trading_dt.date())
                record["remaining"] -= quantity
        if not tax:
            return tax
        quantity_field = "disposed_quantity" if cash_settlement is not None else "sold_quantity"
        lots = [{"original_code": lot.original_code, "buy_date": lot.buy_date.isoformat(),
                 quantity_field: abs(qty)} for lot, qty in close_details]
        if cash_settlement is not None:
            # RQAlpha adds before_trading's return to pre-callback cash. Route the debit through that cash delta.
            self.cash_exit_tax_events.append(dict(code=self.order_book_id, day=trading_dt.date().isoformat(),
                                                  amount=tax, trade_id=None, lots=lots,
                                                  cash_settlement=cash_settlement))
        else:
            self._env.event_bus.publish_event(Event(
                EVENT.PAY_TAXES, order_book_id=self.order_book_id, delta_amount=tax,
                trading_dt=trading_dt, tax_type=TAX_TYPE.DIVIDEND, trade_id=trade_id, lots=lots))
        return tax
