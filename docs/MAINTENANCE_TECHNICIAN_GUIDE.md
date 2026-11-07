# Maintenance Technician guide

## Review an anomaly

Open **Anomalies**, select an authorized device, and inspect the source type and evidence. Add a comment, confirm a fault, mark a false alarm, or request more data.

The Anomalies page groups authorized equipment by device, then parameter, then calendar-time buckets; 10 minutes is the default, with 1-, 5-, 10-, 15-, 30-, and 60-minute options, and each row shows its time range in your browser's display timezone. Only devices assigned to you are available. Each bucket summarizes anomaly count, value range and unit, highest severity, detectors, review status, and unreviewed count. Open a bucket to load its individual anomalies (up to 50 per page), then choose **Open details** to inspect evidence and submit feedback. Bucket pages also contain up to 50 buckets. Use **Flat list** to change presentation; device, parameter, period, source, status, severity, detector and search filters and CSV export remain available.

## Complete an order

Open **Maintenance**, select the assigned order, record inspections or actions, and update the status.

## Enter a manual measurement

Open **Devices**, select a device for which you are the responsible recorder, and add the manual measurement. It remains labelled `manual`.

## View analysis and reports

**Evaluation**, **Analysis**, **Anomalies**, **Maintenance**, and permitted reports are read-supported workflows. Datasets, imports, settings, users, API keys, audit, and tuning are outside the Technician role.

## Permission summary

Technicians can review authorised anomalies, feedback, assigned orders, inspections, actions, and assigned manual measurements. They also have read access to permitted result and report pages according to [PERMISSIONS.md](../PERMISSIONS.md).
