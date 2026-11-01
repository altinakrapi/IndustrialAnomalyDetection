def physical_measurement_issue(
    parameter_name: str, unit: str, value: float
) -> str | None:
    normalized_name = parameter_name.casefold().replace("_", " ").replace("-", " ")
    if normalized_name in {
        "vibration rms",
        "vibration standard deviation",
        "vibration std deviation",
        "vibration std dev",
        "vibration peak",
        "vibration peak to peak",
        "rotational speed",
        "rpm",
        "speed",
        "pressure",
        "current",
    } and value < 0:
        return f"{parameter_name} cannot be negative"
    if normalized_name in {"temperature", "temperature c", "temperature °c"}:
        normalized_unit = unit.strip().casefold()
        if normalized_unit in {"c", "°c", "celsius"} and value < -273.15:
            return "Temperature cannot be below absolute zero"
    return None
