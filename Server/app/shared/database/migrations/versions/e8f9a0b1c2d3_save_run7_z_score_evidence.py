"""Save corrected run 7 Z-score evidence and normalize run 7 spreads.

Revision ID: e8f9a0b1c2d3
Revises: d7e8f9a0b1c2
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8f9a0b1c2d3"
down_revision: Union[str, None] = "d7e8f9a0b1c2"
depends_on: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None


LIMITATION = (
    "Two seeds (101 and 202), about 500 readings each, development data only; "
    "not an independent evaluation."
)

Z_SCORE_PARAMETERS = {"algorithm": "z_score", "threshold": 2.0}
DEFAULT_Z_SCORE_PARAMETERS = {"algorithm": "z_score", "threshold": 3.0}


def upgrade() -> None:
    bind = op.get_bind()
    existing = bind.execute(
        sa.text(
            "SELECT id FROM simulator_tuned_configs "
            "WHERE tuning_run_id = 7 AND algorithm = 'z_score'"
        )
    ).first()
    if existing is None:
        model_result = bind.execute(
            sa.text(
                """
                INSERT INTO model_configs
                    (name, algorithm, description, parameters, contamination, is_active, created_by)
                VALUES
                    (:name, 'z_score', :description, CAST(:parameters AS json), NULL, TRUE, NULL)
                RETURNING id
                """
            ),
            {
                "name": "tuned-z-score-run7-development",
                "description": "Saved simulator tuning candidate for simulated equipment analysis.",
                "parameters": __import__("json").dumps(Z_SCORE_PARAMETERS),
            },
        )
        model_id = model_result.scalar_one()
        evidence = {
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
                    "tuned_false_alarm_rate": 0.021645021645021644,
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
        bind.execute(
            sa.text(
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
                """
            ),
            {
                "model_id": model_id,
                "parameters": __import__("json").dumps(Z_SCORE_PARAMETERS),
                "limitations": LIMITATION,
                "evidence": __import__("json").dumps(evidence),
            },
        )

    # Store tuned-F1 spread, rather than the spread of default-to-tuned improvements.
    for algorithm, spread in (("ewma", 0.00980645161290314), ("cusum", 0.0059172676194220)):
        bind.execute(
            sa.text(
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


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "DELETE FROM simulator_tuned_configs "
            "WHERE tuning_run_id = 7 AND name = 'tuned-z-score-run7-development'"
        )
    )
    bind.execute(
        sa.text(
            "DELETE FROM model_configs "
            "WHERE name = 'tuned-z-score-run7-development'"
        )
    )
