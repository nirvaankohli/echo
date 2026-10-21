"""Conservative pre-call reservations; usage is recorded even if execution is interrupted."""
import json
import math
import os
import time
from datetime import datetime, timezone


class BudgetExceeded(RuntimeError):
    pass


class Budget:
    def __init__(self, store, task_id):
        self.store, self.task_id = store, task_id
        with store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(usage)')}
            for name in ('input_price', 'output_price'):
                if name not in columns:
                    db.execute(f'ALTER TABLE usage ADD COLUMN {name} REAL')

    @staticmethod
    def prices(model):
        try:
            price = json.loads(os.environ.get('ECHO_MODEL_PRICES', '{}')).get(model)
            if price is None:
                return None
            rates = tuple(float(price[key]) for key in ('input', 'output'))
            if any(not math.isfinite(value) or value < 0 for value in rates):
                raise ValueError()
            return rates
        except (ValueError, TypeError, KeyError, AttributeError):
            raise BudgetExceeded('Invalid model prices; use nonnegative finite USD rates per million tokens') from None

    def reserve(self, model, request, output_limit):
        # UTF-8 bytes conservatively bound text tokens; deliberately over-reserves.
        incoming = len(json.dumps(request, ensure_ascii=False,
                                  default=lambda item: item.model_dump(mode='json')).encode('utf-8')) + 1024
        task_limit = int(os.environ.get('ECHO_TASK_TOKEN_LIMIT', '256000'))
        day_limit = int(os.environ.get('ECHO_DAILY_TOKEN_LIMIT', '500000'))
        cap = float(os.environ.get('ECHO_DAILY_USD_LIMIT', '0'))
        if task_limit <= 0 or day_limit <= 0 or not math.isfinite(cap) or cap < 0:
            raise BudgetExceeded('Invalid spending limits')
        if type(output_limit) is not int or output_limit <= 0:
            raise BudgetExceeded('Output token limit must be positive')
        price = self.prices(model)
        if cap > 0 and price is None:
            raise BudgetExceeded('Set verified input/output prices for this model before using a dollar cap')
        dollars = (incoming * price[0] + output_limit * price[1]) / 1e6 if price else 0
        if not math.isfinite(dollars):
            raise BudgetExceeded('Configured prices exceed supported cost range')
        now = time.time()
        day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            task = db.execute('SELECT COALESCE(SUM(input_tokens+output_tokens),0) FROM usage WHERE task_id=?',
                              (self.task_id,)).fetchone()[0]
            daily = db.execute('SELECT COALESCE(SUM(input_tokens+output_tokens),0),COALESCE(SUM(dollars),0) '
                               'FROM usage WHERE created>=?', (day,)).fetchone()
            if task + incoming + output_limit > task_limit:
                raise BudgetExceeded('Task token budget reached; the next call exceeds the per-task allowance')
            if daily[0] + incoming + output_limit > day_limit:
                raise BudgetExceeded('Daily token budget reached; the next call exceeds the daily allowance')
            if cap > 0 and db.execute('SELECT 1 FROM usage WHERE created>=? AND input_price IS NULL LIMIT 1', (day,)).fetchone():
                raise BudgetExceeded('Earlier usage today has no configured price; dollar budget cannot be verified')
            if cap > 0 and daily[1] + dollars > cap:
                raise BudgetExceeded('Daily spending limit reached')
            cur = db.execute('INSERT INTO usage(task_id,model,created,input_tokens,output_tokens,dollars,status,input_price,output_price) '
                             'VALUES (?,?,?,?,?,?,?,?,?)', (self.task_id, model, now, incoming, output_limit, dollars, 'reserved',
                                                          *(price or (None, None))))
            return cur.lastrowid

    def record(self, reservation, result):
        usage = getattr(result, 'usage', None)
        incoming, outgoing = getattr(usage, 'input_tokens', None), getattr(usage, 'output_tokens', None)
        if type(incoming) is not int or type(outgoing) is not int or min(incoming, outgoing) < 0:
            return  # Retain worst-case reservation if provider usage is absent.
        with self.store.db() as db:
            row = db.execute('SELECT * FROM usage WHERE id=?', (reservation,)).fetchone()
            dollars = ((incoming * row['input_price'] + outgoing * row['output_price']) / 1e6
                       if row['input_price'] is not None else row['dollars'])
            db.execute("UPDATE usage SET input_tokens=?,output_tokens=?,dollars=?,status='reported' WHERE id=?",
                       (incoming, outgoing, dollars, reservation))

    def create(self, client, **kwargs):
        reservation = self.reserve(kwargs['model'], kwargs, kwargs['max_output_tokens'])
        result = client.responses.create(**kwargs)
        self.record(reservation, result)
        if result.status != 'completed':
            raise RuntimeError('Model response incomplete')
        return result
