"""RQAlpha 股票持仓扩展：登记日权益、实际到账/可卖日与持有批次税款。

只扩展选定引擎的公司行动/终止事件；订单、成交、现金及持仓队列仍由 RQAlpha 维护。
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
from rqalpha.const import INSTRUMENT_TYPE, POSITION_DIRECTION, TAX_TYPE
from rqalpha.core.events import EVENT, Event
from rqalpha.mod.rqalpha_mod_sys_accounts.position_model import StockPosition
from rqalpha.portfolio.position import Position


class HetuStockPosition(StockPosition):
    __instrument_types__ = (INSTRUMENT_TYPE.CS,)
    rules = None
    action_events = {}
    restrictions = []
    terminal_disposals = {}
    disposition_events = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._rights = {}
        self._applied = set()
        self._bonus_locked = []
        self._tax_records = []
        self._pre_ex_sold = {}

    def __bool__(self):
        return super().__bool__() or bool(self._rights) or self.dividend_tax_payable > 0

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
        return sum(r["dividend"] * r["remaining"] / r["quantity"] * self._tax_rate(r["buy_date"], day)
                   for r in self._tax_records if r["quantity"] > 0)

    @property
    def equity(self):
        return super().equity - self.dividend_tax_payable

    @property
    def closable(self):
        locked = sum(q for release, q in self._bonus_locked if release > self._env.trading_dt.date())
        return max(0, super().closable - locked)

    def before_trading(self, trading_date):
        delta_cash = Position.before_trading(self, trading_date)
        self._unadjusted_prev_close = self.last_price
        self._daily_dividend = 0.0
        self._daily_split = 1.0
        self._bonus_locked = [(d, q) for d, q in self._bonus_locked if d > trading_date]
        if self.direction != POSITION_DIRECTION.LONG:
            return delta_cash
        events = self.action_events.get(self.order_book_id, [])
        for index, event in enumerate(events):
            if event["ex_date"] != trading_date or index in self._applied:
                continue
            lots = self._rights.pop(index, [])
            entitled = sum(q for _, q in lots)
            if event["action_type"] == "cash_dividend":
                amount = entitled * event["d"]
                self._dividend_receivable.append((event["pay_date"], amount))
                self._daily_dividend += amount
                for buy_date, qty in lots:
                    self._tax_records.append({"buy_date": buy_date, "quantity": qty,
                                              "remaining": qty - self._pre_ex_sold.get((index, buy_date), 0),
                                              "dividend": qty * event["d"]})
                if self._quantity:
                    self._avg_price -= amount / self._quantity
                    self._last_price -= amount / self._quantity
            elif event["action_type"] == "bonus_split" and entitled:
                bonus = int((Decimal(entitled) * Decimal(str(event["m"] - 1))).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP))
                old_qty = self._quantity
                self._quantity += bonus
                self._old_quantity += bonus
                # 由引擎持仓队列保留原批次日期；登记后买入的批次不参加送转。
                queue = dict(self.position_queue)
                remaining = bonus
                for n, (buy_date, qty) in enumerate(lots):
                    addition = remaining if n == len(lots) - 1 else round(qty * (event["m"] - 1))
                    queue[buy_date] = queue.get(buy_date, 0) + addition
                    remaining -= addition
                self._queue.set_sate(sorted(queue.items()))
                for record in self._tax_records:
                    if any(buy_date == record["buy_date"] for buy_date, _ in lots):
                        record["quantity"] *= event["m"]
                        record["remaining"] *= event["m"]
                if self._quantity:
                    self._avg_price *= old_qty / self._quantity
                    self._last_price *= old_qty / self._quantity
                if bonus > 0:
                    self._bonus_locked.append((event["stock_listing_date"], bonus))
                self._daily_split = event["m"]
            self._applied.add(index)
        delta_cash += self._handle_dividend_payable(trading_date)
        disposal = self.terminal_disposals.get((self.order_book_id, trading_date.isoformat()))
        if disposal is not None and self.quantity:
            amount = self.quantity * float(disposal["cash_per_share"])
            self._pay_dividend_tax(self._env.trading_dt,
                                   [(buy_day, -qty) for buy_day, qty in self.position_queue])
            self.disposition_events.append(dict(disposal, quantity=self.quantity, cash_amount=amount))
            self._quantity = self._old_quantity = 0
            self._queue.clear()
            delta_cash += amount
        return delta_cash

    def settlement(self, trading_date):
        # 不调用StockPosition默认退市清零/末价返现；仅在明确处置证据路径处理。
        Position.settlement(self, trading_date)
        if self.direction != POSITION_DIRECTION.LONG:
            return 0.0
        for index, event in enumerate(self.action_events.get(self.order_book_id, [])):
            if event["record_date"] == trading_date:
                self._rights[index] = list(self.position_queue)
        if self.quantity and self._instrument.de_listed_at(pd.Timestamp(trading_date)):
            limitation = (trading_date.isoformat(), self.order_book_id, "delisting_disposal_unproven")
            if limitation not in self.restrictions:
                self.restrictions.append(limitation)
        return 0.0

    def _pay_dividend_tax(self, trading_dt, close_details):
        tax = 0.0
        for buy_date, closed_qty in close_details:
            # 登记后、除息前已转让原股：权益仍归原登记批次，税款按真实转让日扣缴。
            for index, lots in self._rights.items():
                event = self.action_events[self.order_book_id][index]
                if event["action_type"] != "cash_dividend":
                    continue
                entitled = sum(q for day, q in lots if day == buy_date)
                used = self._pre_ex_sold.get((index, buy_date), 0)
                quantity = min(abs(closed_qty), max(0, entitled - used))
                tax += quantity * event["d"] * self._tax_rate(buy_date, trading_dt.date())
                self._pre_ex_sold[(index, buy_date)] = used + quantity
            for record in self._tax_records:
                if record["buy_date"] != buy_date:
                    continue
                quantity = min(abs(closed_qty), record["remaining"])
                tax += quantity / record["quantity"] * record["dividend"] * self._tax_rate(
                    buy_date, trading_dt.date())
                record["remaining"] -= quantity
        if tax:
            self._env.event_bus.publish_event(Event(
                EVENT.PAY_TAXES, order_book_id=self.order_book_id, delta_amount=tax,
                trading_dt=trading_dt, tax_type=TAX_TYPE.DIVIDEND))
