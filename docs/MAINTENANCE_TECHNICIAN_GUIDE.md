# Maintenance Technician guide

## Review an anomaly

Open **Anomalies**, select an authorized device, and inspect the source type and evidence. Add a comment, confirm a fault, mark a false alarm, or request more data.

The Anomalies page groups authorized equipment by device, then by parameter. Only devices assigned to you are available, and each device page contains up to 50 measurements. Use **Flat list** to change presentation. Device, period, source, and status filters and CSV export are available. The page is not an episode tree and has no separate unlinked-episode group. Submit feedback and create maintenance orders from an individual anomaly; there is no episode-based maintenance-order workflow. Episode feedback is available through an API operation, not as a separate episode-tree UI action.

## Complete an order

Open **Maintenance**, select the assigned order, record inspections or actions, and update the status.

## Enter a manual measurement

Open **Devices**, select a device for which you are the responsible recorder, and add the manual measurement. It remains labelled `manual`.

## View analysis and reports

**Evaluation**, **Analysis**, **Anomalies**, **Maintenance**, and permitted reports are read-supported workflows. Datasets, imports, settings, users, API keys, audit, and tuning are outside the Technician role.

## Permission summary

Technicians can review authorised anomalies, feedback, assigned orders, inspections, actions, and assigned manual measurements. They also have read access to permitted result and report pages according to [PERMISSIONS.md](../PERMISSIONS.md).
