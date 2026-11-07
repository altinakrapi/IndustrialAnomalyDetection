# Permission matrix

Generated automatically from FastAPI OpenAPI and route-function dependency declarations. Authentication routes explicitly listed as public are outside RBAC; protected application routes require authentication and/or role dependencies.

| Method | Route | Roles allowed | Permission check |
|---|---|---|---|
| POST | `/api/v1/analyses` | administrator, data_analyst | `app\modules\analyses\api\router.py` |
| GET | `/api/v1/analyses` | all authenticated | `app\modules\analyses\api\router.py` |
| POST | `/api/v1/analyses/compare` | all authenticated | `app\modules\analyses\api\router.py` |
| POST | `/api/v1/analyses/comparisons` | administrator, data_analyst | `app\modules\analyses\api\router.py` |
| GET | `/api/v1/analyses/comparisons/{comparison_id}` | administrator, data_analyst | `app\modules\analyses\api\router.py` |
| GET | `/api/v1/analyses/comparisons/{comparison_id}/csv` | administrator, data_analyst | `app\modules\analyses\api\router.py` |
| GET | `/api/v1/analyses/detectors` | all authenticated | `app\modules\analyses\api\router.py` |
| POST | `/api/v1/analyses/equipment-history` | administrator, data_analyst | `app\modules\analyses\api\router.py` |
| POST | `/api/v1/analyses/evaluations` | administrator, data_analyst | `app\modules\analyses\api\simulator_evaluations.py` |
| GET | `/api/v1/analyses/evaluations` | all authenticated | `app\modules\analyses\api\simulator_evaluations.py` |
| POST | `/api/v1/analyses/evaluations/ground-truth` | administrator, data_analyst | `app\modules\analyses\api\simulator_evaluations.py` |
| GET | `/api/v1/analyses/evaluations/ground-truth` | all authenticated | `app\modules\analyses\api\simulator_evaluations.py` |
| GET | `/api/v1/analyses/evaluations/tuned-configs` | administrator, data_analyst | `app\modules\analyses\api\simulator_tuning.py` |
| POST | `/api/v1/analyses/evaluations/tuned-configs` | administrator, data_analyst | `app\modules\analyses\api\simulator_tuning.py` |
| GET | `/api/v1/analyses/evaluations/tuning-runs` | administrator, data_analyst | `app\modules\analyses\api\simulator_tuning.py` |
| POST | `/api/v1/analyses/evaluations/tuning-runs` | administrator, data_analyst | `app\modules\analyses\api\simulator_tuning.py` |
| POST | `/api/v1/analyses/evaluations/tuning-runs/{tuning_run_id}/reserved-comparison` | administrator, data_analyst | `app\modules\analyses\api\simulator_tuning.py` |
| GET | `/api/v1/analyses/evaluations/{evaluation_id}` | all authenticated | `app\modules\analyses\api\simulator_evaluations.py` |
| GET | `/api/v1/analyses/evaluations/{evaluation_id}/csv` | all authenticated | `app\modules\analyses\api\simulator_evaluations.py` |
| GET | `/api/v1/analyses/evaluations/{evaluation_id}/pdf` | all authenticated | `app\modules\analyses\api\simulator_evaluations.py` |
| GET | `/api/v1/analyses/history` | all authenticated | `app\modules\analyses\api\router.py` |
| GET | `/api/v1/analyses/models` | all authenticated | `app\modules\analyses\api\router.py` |
| POST | `/api/v1/analyses/models` | administrator, data_analyst | `app\modules\analyses\api\router.py` |
| PATCH | `/api/v1/analyses/models/{model_id}` | all authenticated | `app\modules\analyses\api\router.py` |
| GET | `/api/v1/analyses/summary` | all authenticated | `app\modules\analyses\api\router.py` |
| GET | `/api/v1/analyses/{analysis_id}` | all authenticated | `app\modules\analyses\api\router.py` |
| GET | `/api/v1/analyses/{analysis_id}/results` | all authenticated | `app\modules\analyses\api\router.py` |
| GET | `/api/v1/analyses/{analysis_id}/timeline` | all authenticated | `app\modules\analyses\api\router.py` |
| GET | `/api/v1/anomalies` | all authenticated | `app\modules\anomaly_detection\api\router.py` |
| POST | `/api/v1/anomalies/alert-episodes/{episode_id}/feedback` | administrator, maintenance_technician | `app\modules\anomaly_detection\api\router.py` |
| GET | `/api/v1/anomalies/devices/grouped` | all authenticated | `app\modules\anomaly_detection\api\router.py` |
| GET | `/api/v1/anomalies/devices/{device_id}/page` | all authenticated | `app\modules\anomaly_detection\api\router.py` |
| GET | `/api/v1/anomalies/export.csv` | all authenticated | `app\modules\anomaly_detection\api\router.py` |
| GET | `/api/v1/anomalies/feedback/export.csv` | all authenticated | `app\modules\anomaly_detection\api\router.py` |
| GET | `/api/v1/anomalies/feedback/stats` | all authenticated | `app\modules\anomaly_detection\api\router.py` |
| GET | `/api/v1/anomalies/page` | all authenticated | `app\modules\anomaly_detection\api\router.py` |
| GET | `/api/v1/anomalies/repeat-cases` | all authenticated | `app\modules\anomaly_detection\api\router.py` |
| GET | `/api/v1/anomalies/summary` | all authenticated | `app\modules\anomaly_detection\api\router.py` |
| GET | `/api/v1/anomalies/{anomaly_id}` | all authenticated | `app\modules\anomaly_detection\api\router.py` |
| POST | `/api/v1/anomalies/{anomaly_id}/feedback` | administrator, maintenance_technician | `app\modules\anomaly_detection\api\router.py` |
| POST | `/api/v1/anomalies/{anomaly_id}/maintenance-order` | all authenticated | `app\modules\anomaly_detection\api\router.py` |
| POST | `/api/v1/anomalies/{anomaly_id}/review` | administrator, maintenance_technician | `app\modules\anomaly_detection\api\router.py` |
| GET | `/api/v1/audit` | administrator | `app\modules\audit_logs\api\router.py` |
| POST | `/api/v1/auth/change-password` | all authenticated | `app\modules\users\api\auth.py` |
| POST | `/api/v1/auth/forgot-password` | public | `app\modules\users\api\auth.py` |
| POST | `/api/v1/auth/login` | public | `app\modules\users\api\auth.py` |
| GET | `/api/v1/auth/me` | all authenticated | `app\modules\users\api\auth.py` |
| PATCH | `/api/v1/auth/me` | all authenticated | `app\modules\users\api\auth.py` |
| POST | `/api/v1/auth/register` | public | `app\modules\users\api\auth.py` |
| POST | `/api/v1/auth/reset-password` | public | `app\modules\users\api\auth.py` |
| GET | `/api/v1/auth/status` | public | `app\modules\users\api\auth.py` |
| GET | `/api/v1/dashboard` | all authenticated | `app\modules\dashboard\api\router.py` |
| POST | `/api/v1/datasets` | administrator, data_analyst | `app\modules\datasets\api\router.py` |
| GET | `/api/v1/datasets` | administrator, data_analyst | `app\modules\datasets\api\router.py` |
| POST | `/api/v1/datasets/nasa-ims/evaluations` | administrator, data_analyst | `app\modules\datasets\api\nasa_ims_validation.py` |
| GET | `/api/v1/datasets/nasa-ims/evaluations` | all authenticated | `app\modules\datasets\api\nasa_ims_validation.py` |
| GET | `/api/v1/datasets/nasa-ims/evaluations/facts` | all authenticated | `app\modules\datasets\api\nasa_ims_validation.py` |
| GET | `/api/v1/datasets/nasa-ims/evaluations/{validation_id}` | all authenticated | `app\modules\datasets\api\nasa_ims_validation.py` |
| GET | `/api/v1/datasets/nasa-ims/evaluations/{validation_id}/csv` | all authenticated | `app\modules\datasets\api\nasa_ims_validation.py` |
| GET | `/api/v1/datasets/nasa-ims/evaluations/{validation_id}/pdf` | all authenticated | `app\modules\datasets\api\nasa_ims_validation.py` |
| GET | `/api/v1/datasets/nasa-ims/import-status` | administrator, data_analyst | `app\modules\datasets\api\router.py` |
| POST | `/api/v1/datasets/nasa-ims/initialize` | administrator, data_analyst | `app\modules\datasets\api\router.py` |
| GET | `/api/v1/datasets/{dataset_id}` | administrator, data_analyst | `app\modules\datasets\api\router.py` |
| PATCH | `/api/v1/datasets/{dataset_id}/archive` | administrator, data_analyst | `app\modules\datasets\api\router.py` |
| POST | `/api/v1/datasets/{dataset_id}/compare` | administrator, data_analyst | `app\modules\datasets\api\router.py` |
| GET | `/api/v1/datasets/{dataset_id}/comparison` | administrator, data_analyst | `app\modules\datasets\api\router.py` |
| GET | `/api/v1/datasets/{dataset_id}/download` | administrator, data_analyst | `app\modules\datasets\api\router.py` |
| GET | `/api/v1/datasets/{dataset_id}/exploration` | administrator, data_analyst | `app\modules\datasets\api\router.py` |
| GET | `/api/v1/datasets/{dataset_id}/statistics` | administrator, data_analyst | `app\modules\datasets\api\router.py` |
| POST | `/api/v1/device-api-keys/devices/{device_id}` | administrator | `app\modules\sensor_ingestion\api.py` |
| GET | `/api/v1/device-api-keys/devices/{device_id}` | administrator | `app\modules\sensor_ingestion\api.py` |
| POST | `/api/v1/device-api-keys/devices/{device_id}/rotate` | administrator | `app\modules\sensor_ingestion\api.py` |
| DELETE | `/api/v1/device-api-keys/devices/{device_id}/{key_id}` | administrator | `app\modules\sensor_ingestion\api.py` |
| GET | `/api/v1/devices` | all authenticated | `app\modules\devices\api\router.py` |
| POST | `/api/v1/devices` | administrator, data_analyst | `app\modules\devices\api\router.py` |
| POST | `/api/v1/devices/admin/archive-test-data` | all authenticated | `app\modules\devices\api\router.py` |
| POST | `/api/v1/devices/admin/archive-test-data/dry-run` | all authenticated | `app\modules\devices\api\router.py` |
| GET | `/api/v1/devices/choices` | all authenticated | `app\modules\devices\api\router.py` |
| POST | `/api/v1/devices/measurement-severity/dry-run` | all authenticated | `app\modules\devices\api\router.py` |
| POST | `/api/v1/devices/measurement-severity/store` | all authenticated | `app\modules\devices\api\router.py` |
| GET | `/api/v1/devices/responsible-users` | all authenticated | `app\modules\devices\api\router.py` |
| POST | `/api/v1/devices/severity/recompute` | all authenticated | `app\modules\devices\api\router.py` |
| POST | `/api/v1/devices/severity/recompute/dry-run` | all authenticated | `app\modules\devices\api\router.py` |
| GET | `/api/v1/devices/{device_id}` | all authenticated | `app\modules\devices\api\router.py` |
| DELETE | `/api/v1/devices/{device_id}` | administrator, data_analyst | `app\modules\devices\api\router.py` |
| PATCH | `/api/v1/devices/{device_id}` | administrator, data_analyst | `app\modules\devices\api\router.py` |
| PATCH | `/api/v1/devices/{device_id}/archive` | administrator, data_analyst | `app\modules\devices\api\router.py` |
| GET | `/api/v1/devices/{device_id}/measurements` | all authenticated | `app\modules\devices\api\equipment.py` |
| POST | `/api/v1/devices/{device_id}/measurements` | administrator, data_analyst, maintenance_technician | `app\modules\devices\api\equipment.py` |
| GET | `/api/v1/devices/{device_id}/parameters` | all authenticated | `app\modules\devices\api\equipment.py` |
| POST | `/api/v1/devices/{device_id}/parameters` | administrator, data_analyst, maintenance_technician | `app\modules\devices\api\equipment.py` |
| PATCH | `/api/v1/devices/{device_id}/parameters/{parameter_id}` | administrator, data_analyst, maintenance_technician | `app\modules\devices\api\equipment.py` |
| GET | `/api/v1/maintenance` | all authenticated | `app\modules\maintenance\api\router.py` |
| POST | `/api/v1/maintenance` | administrator, data_analyst | `app\modules\maintenance\api\router.py` |
| GET | `/api/v1/maintenance/technicians` | all authenticated | `app\modules\maintenance\api\router.py` |
| PATCH | `/api/v1/maintenance/{order_id}` | administrator, maintenance_technician | `app\modules\maintenance\api\router.py` |
| POST | `/api/v1/maintenance/{order_id}/actions` | administrator, maintenance_technician | `app\modules\maintenance\api\router.py` |
| GET | `/api/v1/monitoring` | all authenticated | `app\modules\monitoring\api\router.py` |
| PATCH | `/api/v1/monitoring` | administrator | `app\modules\monitoring\api\router.py` |
| GET | `/api/v1/monitoring/dashboard` | all authenticated | `app\modules\monitoring\api\router.py` |
| POST | `/api/v1/monitoring/run` | administrator, data_analyst | `app\modules\monitoring\api\router.py` |
| GET | `/api/v1/notifications` | all authenticated | `app\modules\notifications\api\router.py` |
| POST | `/api/v1/notifications/archive` | administrator | `app\modules\notifications\api\router.py` |
| POST | `/api/v1/notifications/archive/dry-run` | administrator | `app\modules\notifications\api\router.py` |
| POST | `/api/v1/notifications/read-all` | all authenticated | `app\modules\notifications\api\router.py` |
| POST | `/api/v1/notifications/restore` | administrator | `app\modules\notifications\api\router.py` |
| POST | `/api/v1/notifications/restore/dry-run` | administrator | `app\modules\notifications\api\router.py` |
| GET | `/api/v1/notifications/unread-count` | all authenticated | `app\modules\notifications\api\router.py` |
| PATCH | `/api/v1/notifications/{notification_id}/read` | all authenticated | `app\modules\notifications\api\router.py` |
| POST | `/api/v1/readings` | all authenticated | `app\modules\sensor_ingestion\api.py` |
| POST | `/api/v1/readings/batch` | all authenticated | `app\modules\sensor_ingestion\api.py` |
| GET | `/api/v1/reports/analysis/{analysis_id}.pdf` | administrator, data_analyst | `app\modules\reports\api\router.py` |
| GET | `/api/v1/reports/device/{device_id}.pdf` | administrator, data_analyst, maintenance_technician | `app\modules\reports\api\router.py` |
| GET | `/api/v1/reports/history` | all authenticated | `app\modules\reports\api\router.py` |
| GET | `/api/v1/reports/maintenance.csv` | administrator, data_analyst, maintenance_technician | `app\modules\reports\api\router.py` |
| GET | `/api/v1/settings` | administrator | `app\modules\settings\api\router.py` |
| PATCH | `/api/v1/settings` | administrator | `app\modules\settings\api\router.py` |
| GET | `/api/v1/users` | administrator | `app\modules\users\api\router.py` |
| POST | `/api/v1/users` | administrator | `app\modules\users\api\router.py` |
| PATCH | `/api/v1/users/{user_id}` | administrator | `app\modules\users\api\router.py` |
| DELETE | `/api/v1/users/{user_id}` | administrator | `app\modules\users\api\router.py` |
| POST | `/api/v1/users/{user_id}/reset-password` | administrator | `app\modules\users\api\router.py` |
| GET | `/health` | all authenticated | `public or application-defined dependency` |
