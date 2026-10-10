"""Step 4: train the trip-time model on data/trips.csv (date-based split)."""
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

SEED = 42
ROOT = Path(__file__).resolve().parent.parent
NUMERIC = ["departure_hour", "weekday", "is_holiday", "rain_mm", "rain_prev3h_mm"]
FEATURES = ["route_id"] + NUMERIC
LABEL = "trip_minutes"


def build_pipeline() -> Pipeline:
    pre = ColumnTransformer([
        ("route", OneHotEncoder(handle_unknown="ignore"), ["route_id"]),
        ("num", "passthrough", NUMERIC),
    ])
    gbr = GradientBoostingRegressor(n_estimators=200, max_depth=4, random_state=SEED)
    return Pipeline([("prep", pre), ("model", gbr)])


def main() -> None:
    trips_file = ROOT / "data" / "trips.csv"
    if not trips_file.exists():
        print(f"[train] missing {trips_file}. Create it with: python ml/simulate.py")
        return
    df = pd.read_csv(trips_file)

    dates = sorted(df["date"].unique())
    cut = int(len(dates) * 0.8)
    test_start = dates[cut]
    train = df[df["date"] < test_start]
    test = df[df["date"] >= test_start]
    print(f"[train] train {len(train)} rows, test {len(test)} rows, test starts {test_start}")

    # Baseline: mean per route_id + departure_hour from the training set
    means = train.groupby(["route_id", "departure_hour"])[LABEL].mean().rename("pred")
    base_pred = test.join(means, on=["route_id", "departure_hour"])["pred"].fillna(train[LABEL].mean())
    baseline_mae = mean_absolute_error(test[LABEL], base_pred)

    pipe = build_pipeline()
    pipe.fit(train[FEATURES], train[LABEL])
    pred = pipe.predict(test[FEATURES])
    model_mae = mean_absolute_error(test[LABEL], pred)
    within5 = float(np.mean(np.abs(pred - test[LABEL].values) <= 5) * 100)

    print(f"[train] baseline MAE = {baseline_mae:.2f} min")
    print(f"[train] model MAE    = {model_mae:.2f} min")
    print(f"[train] within 5 min = {within5:.1f}%")
    if model_mae > 4:
        print("[train] WARNING: model MAE is above 4 minutes")
    if model_mae >= baseline_mae:
        print("[train] WARNING: model is not better than the baseline")

    (ROOT / "model").mkdir(exist_ok=True)
    joblib.dump(pipe, ROOT / "model" / "model.joblib")
    metrics = {
        "baseline_mae": round(float(baseline_mae), 3), "model_mae": round(float(model_mae), 3),
        "within_5min_pct": round(within5, 1), "n_train": len(train), "n_test": len(test),
        "test_start_date": test_start,
    }
    (ROOT / "model" / "metrics.json").write_text(json.dumps(metrics, indent=2))
    print(f"[train] saved model/model.joblib and model/metrics.json")


if __name__ == "__main__":
    main()
