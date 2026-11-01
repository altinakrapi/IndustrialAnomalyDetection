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

## Source type

Accepted physical or external readings are stored with `source_type=sensor`. A simulated gateway uses the simulator provisioning workflow and stores `source_type=simulated`.

The API can support a sensor installation. Documentation does not imply that every deployment has physical sensor infrastructure connected.
