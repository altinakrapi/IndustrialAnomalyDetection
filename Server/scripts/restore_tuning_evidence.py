import json
import re
from sqlalchemy import create_engine, text

from app.core.config import settings


LIMITATION = (
    "Two seeds (101 and 202), about 500 readings each, development data only; "
    "not an independent evaluation."
)

Z_SCORE_PARAMETERS = {"algorithm": "z_score", "threshold": 2.0}
DEFAULT_Z_SCORE_PARAMETERS = {"algorithm": "z_score", "threshold": 3.0}

EVIDENCE = {
    "tuning_run_id": 7,
    "selected_parameters": Z_SCORE_PARAMETERS,
    "default_parameters": DEFAULT_Z_SCORE_PARAMETERS,
    "objective": "maximize_f1_with_false_alarm_cap",
    "false_alarm_cap": 0.05,
    "data_used": {"ground_truth_run_ids": [11, 12], "seeds": [101, 202]},
    "runtime_seconds": 18.192587199999252,
    "candidate_count": 4,
    "default_vs_tuned_by_run": [
        {
            "run_id": 11,
            "seed": 101,
            "default_f1": 0.624390243902439,
            "default_false_alarm_rate": 0.0,
            "tuned_f1": 0.7079646017699115,
            "tuned_false_alarm_rate": 0.021645021645021645,
        },
        {
            "run_id": 12,
            "seed": 202,
            "default_f1": 0.6310679611650485,
            "default_false_alarm_rate": 0.0,
            "tuned_f1": 0.6944444444444444,
            "tuned_false_alarm_rate": 0.0,
        },
    ],
    "mean_f1": {
        "default": 0.6277291025337438,
        "tuned": 0.701204523107178,
    },
    "seed_to_seed_f1_spread": 0.0135201573254671,
    "limitations": LIMITATION,
}


def database_url() -> str:
    return str(settings.DATABASE_URL)


def main() -> None:
    engine = create_engine(database_url())
    try:
        with engine.begin() as connection:
            tuning_run = connection.execute(
                text("SELECT id FROM simulator_tuning_runs WHERE id = 7")
            ).first()
            if tuning_run is None:
                raise SystemExit(
                    "simulator_tuning_runs.id = 7 is missing; evidence was not created automatically."
                )

            model = connection.execute(
                text(
                    """
                    SELECT id FROM model_configs
                    WHERE name = 'tuned-z-score-run7-development'
                    """
                )
            ).first()
            if model is None:
                model = connection.execute(
                    text(
                        """
                        INSERT INTO model_configs
                            (name, algorithm, description, parameters, contamination, is_active, created_by)
                        VALUES
                            ('tuned-z-score-run7-development', 'z_score',
                             'Saved simulator tuning candidate for simulated equipment analysis.',
                             CAST(:parameters AS json), NULL, TRUE, NULL)
                        RETURNING id
                        """
                    ),
                    {"parameters": json.dumps(Z_SCORE_PARAMETERS)},
                ).first()

            evidence = dict(EVIDENCE)
            evidence_json = json.dumps(evidence)
            connection.execute(
                text(
                    """
                    INSERT INTO simulator_tuned_configs
                        (tuning_run_id, model_config_id, name, algorithm, parameters, objective,
                         false_alarm_cap, development_run_ids, development_seeds, limitations,
                         evidence, is_superseded, created_by)
                    VALUES
                        (7, :model_id, 'tuned-z-score-run7-development', 'z_score',
                         CAST(:parameters AS json), 'maximize_f1_with_false_alarm_cap', 0.05,
                         CAST('[11, 12]' AS json), CAST('[101, 202]' AS json), :limitations,
                         CAST(:evidence AS json), FALSE, NULL)
                    ON CONFLICT (name) DO UPDATE SET
                        tuning_run_id = EXCLUDED.tuning_run_id,
                        model_config_id = EXCLUDED.model_config_id,
                        algorithm = EXCLUDED.algorithm,
                        parameters = EXCLUDED.parameters,
                        objective = EXCLUDED.objective,
                        false_alarm_cap = EXCLUDED.false_alarm_cap,
                        development_run_ids = EXCLUDED.development_run_ids,
                        development_seeds = EXCLUDED.development_seeds,
                        limitations = EXCLUDED.limitations,
                        evidence = EXCLUDED.evidence,
                        is_superseded = EXCLUDED.is_superseded
                    """
                ),
                {
                    "model_id": model.id,
                    "parameters": json.dumps(Z_SCORE_PARAMETERS),
                    "limitations": LIMITATION,
                    "evidence": evidence_json,
                },
            )
            for algorithm, spread in (
                ("ewma", 0.00980645161290314),
                ("cusum", 0.0059172676194220),
            ):
                connection.execute(
                    text(
                        """
                        UPDATE simulator_tuned_configs
                        SET evidence = jsonb_set(
                            CAST(evidence AS jsonb),
                            '{seed_to_seed_f1_spread}',
                            CAST(CAST(:spread AS text) AS jsonb),
                            true
                        )
                        WHERE tuning_run_id = 7 AND algorithm = :algorithm
                        """
                    ),
                    {"algorithm": algorithm, "spread": str(spread)},
                )
    finally:
        engine.dispose()

    print("Run 7 tuned Z-score evidence restored or confirmed idempotently.")


if __name__ == "__main__":
    main()
