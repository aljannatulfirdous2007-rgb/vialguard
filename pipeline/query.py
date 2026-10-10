"""Read results back out of InfluxDB (Flux queries) to show the pipeline end to end.

  python pipeline/query.py
Prints: readings per device, hash-chain breaks, event counts, and how accurate each streamed forecast was
(forecast made at time t compared with the reading actually measured at t + 10 min).
"""
import config
from influxdb_client import InfluxDBClient

B = config.INFLUX_BUCKET
RANGE = "range(start: 2020-01-01T00:00:00Z)"


def scalar_table(q, client):
    out = []
    for table in client.query_api().query(q):
        for rec in table.records:
            out.append(rec.values)
    return out


def main():
    c = InfluxDBClient(url=config.INFLUX_URL, token=config.INFLUX_TOKEN, org=config.INFLUX_ORG, timeout=60000)
    rows = scalar_table(f'from(bucket:"{B}") |> {RANGE} |> filter(fn:(r)=> r._measurement=="reading" and r._field=="temp") '
                        f'|> group(columns:["device"]) |> count()', c)
    print("readings in InfluxDB:")
    for r in rows:
        print(f"  {r['device']:<14} {r['_value']}")
    rows = scalar_table(f'from(bucket:"{B}") |> {RANGE} |> filter(fn:(r)=> r._measurement=="reading" and r._field=="chain_ok") '
                        f'|> filter(fn:(r)=> r._value==0) |> group() |> count()', c)
    print("hash-chain breaks:", rows[0]["_value"] if rows else 0)
    rows = scalar_table(f'from(bucket:"{B}") |> {RANGE} |> filter(fn:(r)=> r._measurement=="event") '
                        f'|> group(columns:["kind"]) |> count()', c)
    print("events:", ", ".join(f"{r['kind']}={r['_value']}" for r in rows) or "none")

    # live forecast accuracy: shift each forecast forward 10 min and join with the measured temperature
    for model in ("xgboost", "lstm"):
        q = f'''
actual = from(bucket:"{B}") |> {RANGE} |> filter(fn:(r)=> r._measurement=="reading" and r._field=="temp")
  |> aggregateWindow(every: 1m, fn: mean, createEmpty: false) |> keep(columns:["_time","_value","device"])
pred = from(bucket:"{B}") |> {RANGE} |> filter(fn:(r)=> r._measurement=="forecast" and r.model=="{model}" and r._field=="temp_10m")
  |> timeShift(duration: 10m) |> aggregateWindow(every: 1m, fn: mean, createEmpty: false) |> keep(columns:["_time","_value","device"])
join(tables:{{a: actual, p: pred}}, on:["_time","device"])
  |> map(fn:(r)=> ({{r with err: math.abs(x: r._value_a - r._value_p)}}))
  |> group() |> mean(column:"err")'''
        q = "import \"math\"\n" + q
        try:
            rows = scalar_table(q, c)
            cnt = scalar_table(f'from(bucket:"{B}") |> {RANGE} |> filter(fn:(r)=> r._measurement=="forecast" and r.model=="{model}" and r._field=="temp_10m") |> group() |> count()', c)
            if rows:
                print(f"{model} streamed forecasts: {cnt[0]['_value'] if cnt else 0}, mean error vs reading 10 min later: {rows[0]['err']:.3f} °C"
                      " (all 4 days incl. training period; held-out test numbers are in data/ml_report.json)")
            else:
                print(f"{model}: no forecasts in InfluxDB")
        except Exception as e:
            print(f"{model}: query failed: {e}")
    c.close()


if __name__ == "__main__":
    main()
