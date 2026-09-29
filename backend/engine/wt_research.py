"""Incremental WT analytical trades, using the same lifecycle as chart plans."""
from copy import deepcopy
import heapq
from .wt_trades import create_trade, advance_trade, empty_bucket, count_trade, summarize

SETUPS = ('T1', 'T2', 'T3', 'T4')


class WTResearch:
    def __init__(self, parameters):
        self.parameters = parameters
        self.active = {}
        self.closed = []
        self.buckets = {s: empty_bucket() for s in SETUPS}
        self.executed = []
        self.last_bar = None

    def update(self, snapshot, bar, setup_events, ready_event, bar_index):
        p = self.parameters
        changed = []
        if not p['researchMode']:
            return changed
        # Research observes confirmed historical states. Explicit ordered ticks
        # can be supplied by a feed/replay adapter without inventing OHLC order.
        if not bar.get('confirmed', True) and 'ticks' not in bar:
            return changed
        if self.last_bar == bar['start'] and 'ticks' not in bar:
            return changed
        self.last_bar = bar['start']
        cutoff = (bar['start'] - p['researchDays'] * 86400000 if p['researchPeriodMode'] == 'Days'
                  else bar_index - p['researchBars'] + 1)
        while self.closed and self.closed[0][0] < cutoff:
            _, _, old = heapq.heappop(self.closed)
            for setup in old['setups']:
                count_trade(self.buckets[setup], old, -1)
        for key, trade in list(self.active.items()):
            advance_trade(trade, bar, p['researchAmbiguous'], p['researchMoveToBE'], bar.get('ticks'))
            changed.append(deepcopy(trade))
            if not trade['open']:
                del self.active[key]
                if trade['window_key'] >= cutoff:
                    heapq.heappush(self.closed, (trade['window_key'], key, deepcopy(trade)))
                    for setup in trade['setups']:
                        count_trade(self.buckets[setup], trade)
        candidates = setup_events if p['researchEntryMode'] == 'SETUP SIGNAL' else []
        generation = snapshot['setup_generation_id']
        if p['researchEntryMode'] == 'ENTER NOW' and ready_event and generation not in self.executed:
            self.executed = (self.executed + [generation])[-256:]
            candidates = [dict(snapshot, entry=bar['close'], sl=snapshot.get('initial_sl', snapshot.get('sl')))]
        for event in candidates:
            busy = {s for trade in self.active.values() for s in trade['setups']}
            setups = [s for s in event['setups'] if s not in busy]
            if not setups:
                continue
            # One execution record, with several attribution memberships.
            key = f"{generation}:{p['researchEntryMode']}:{bar['start']}:{'+'.join(setups)}"
            try:
                trade = create_trade(dict(event, setups=setups), bar['close'], event['sl'],
                                     snapshot['event_time'], [p[f'r{i}'] for i in range(1, 5)],
                                     [p[f'researchTp{i}Weight'] for i in range(1, 5)], p['researchEntryMode'], key)
            except ValueError:
                continue
            trade['window_key'] = bar['start'] if p['researchPeriodMode'] == 'Days' else bar_index
            self.active[key] = trade
            changed.append(deepcopy(trade))
        return changed

    def summary(self):
        p = self.parameters
        busy = {s for t in self.active.values() for s in t['setups']}
        return dict(enabled=p['researchMode'], entry_mode=p['researchEntryMode'],
                    period_mode=p['researchPeriodMode'], days=p['researchDays'], bars=p['researchBars'],
                    move_to_be=p['researchMoveToBE'], ambiguous_policy=p['researchAmbiguous'],
                    allocation=[p[f'researchTp{i}Weight'] / sum(p[f'researchTp{j}Weight'] for j in range(1, 5)) for i in range(1, 5)],
                    buckets={s: summarize(self.buckets[s], s in busy, p['riskUsdt']) for s in SETUPS})

    def export_state(self):
        return deepcopy(dict(active=self.active, closed=self.closed, buckets=self.buckets,
                             executed=self.executed, last_bar=self.last_bar))

    def restore_state(self, state):
        for key in ('active', 'closed', 'buckets', 'executed', 'last_bar'):
            setattr(self, key, deepcopy(state[key]))
        # JSON checkpoints convert heap tuples to lists; normalise before push.
        self.closed = [(key, identity, trade) for key, identity, trade in self.closed]
        heapq.heapify(self.closed)
