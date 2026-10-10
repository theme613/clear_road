"""Train the road congestion model on data/congestion.csv (date-based split)."""
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

SEED = 42
ROOT = Path(__file__).resolve().parent.parent
FEATURES = ["departure_hour", "weekday", "is_holiday", "rain_mm", "rain_prev3h_mm",
            "trip_km", "inbound"]
LABEL = "congestion_mult"


def main() -> None:
    src = ROOT / "data" / "congestion.csv"
    if not src.exists():
        print(f"[train_congestion] missing {src}. Create it with: python ml/simulate_congestion.py")
        return
    df = pd.read_csv(src)
    dates = sorted(df["date"].unique())
    test_start = dates[int(len(dates) * 0.8)]
    train, test = df[df["date"] < test_start], df[df["date"] >= test_start]
    print(f"[train_congestion] train {len(train)} rows, test {len(test)} rows, test starts {test_start}")

    # Baseline: mean multiplier per departure hour (ignores rain, day, direction)
    means = train.groupby("departure_hour")[LABEL].mean().rename("pred")
    base_pred = test.join(means, on="departure_hour")["pred"].fillna(train[LABEL].mean())
    baseline_mae = mean_absolute_error(test[LABEL], base_pred)

    model = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.08, random_state=SEED)
    model.fit(train[FEATURES], train[LABEL])
    pred = model.predict(test[FEATURES])
    model_mae = mean_absolute_error(test[LABEL], pred)
    # In minutes for a typical 40-minute free-flow trip, easier to read
    typical = 40.0
    print(f"[train_congestion] baseline MAE = {baseline_mae:.3f} (~{baseline_mae * typical:.1f} min on a 40-min trip)")
    print(f"[train_congestion] model MAE    = {model_mae:.3f} (~{model_mae * typical:.1f} min on a 40-min trip)")
    if model_mae >= baseline_mae:
        print("[train_congestion] WARNING: model is not better than the baseline")

    (ROOT / "model").mkdir(exist_ok=True)
    joblib.dump(model, ROOT / "model" / "congestion.joblib")
    metrics = {"baseline_mae": round(float(baseline_mae), 4), "model_mae": round(float(model_mae), 4),
               "within_10pct": round(float(np.mean(np.abs(pred / test[LABEL].values - 1) <= 0.10) * 100), 1),
               "n_train": len(train), "n_test": len(test), "test_start_date": test_start}
    (ROOT / "model" / "congestion_metrics.json").write_text(json.dumps(metrics, indent=2))
    print(f"[train_congestion] saved model/congestion.joblib and model/congestion_metrics.json")


if __name__ == "__main__":
    main()
