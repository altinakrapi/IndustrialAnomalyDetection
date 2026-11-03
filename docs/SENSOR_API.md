# Sensor API

External sensors or gateways submit measurements through the sensor-ingestion API using a device API key.

## Device key provisioning

An Administrator creates and revokes device keys under **Devices**. The plaintext key is secret and is shown only when issued.

## Ingest readings

The OpenAPI payload is authoritative. Its route is:

```text
POST /api/v1/readings/batch
```

Authenticate with the device key header required by the API's interactive documentation at `/docs`.

The ingestion pipeline validates key ownership, active status, parameter units, timestamps, values, batch size, rate limits, and supported idempotency behavior.

The no-secret configuration is a request-time contract: never place device keys in source control, report exports, screenshots, or application logs.

## Rate limits

- Each device is limited to 120 readings in any rolling 60-second window by default. The limit is configurable in Settings as `sensor_ingestion_limit_per_minute`.
- A batch is atomic: it is accepted in full or rejected in full.
- The readings limit is enforced by reading count, not request count. Rejected batches do not consume capacity.
- Every sensor-ingest IP has a separate global request ceiling of 1200 requests per minute by default. This guard is configurable with `SENSOR_INGESTION_GLOBAL_REQUEST_LIMIT_PER_MINUTE` and cannot starve normal device traffic at the default.
- Invalid, missing, or revoked keys are rejected before ingestion and are limited per source IP.

Rate-limit responses use `Retry-After`, `RateLimit-Limit`, `RateLimit-Remaining`, and `RateLimit-Reset`. `Retry-After` and `RateLimit-Reset` are seconds until sufficient capacity is available.

## Source type

Accepted physical or external readings are stored with `source_type=sensor`. A simulated gateway uses the simulator provisioning workflow and stores `source_type=simulated`.

The API can support a sensor installation. Documentation does not imply that every deployment has physical sensor infrastructure connected.
