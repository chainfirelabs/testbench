"""Shared component fixtures for the development seed scripts."""


def components_for(name, version, count):
    labels = ("Core", "Agent", "CLI", "Web Console", "API", "Reporting")
    return [
        {"name": f"{name} {labels[i] if i < len(labels) else f'Component {i + 1}'}",
         "version": version}
        for i in range(count)
    ]


def component_additions(existing, name, version, count):
    """Return enough distinct components to reach count, preserving current ones."""
    missing = max(0, count - len(existing))
    if not missing:
        return []
    known = {(item["name"].casefold(), str(item.get("version") or "")) for item in existing}
    additions = []
    for candidate in components_for(name, version, count + len(existing)):
        key = (candidate["name"].casefold(), candidate["version"])
        if key not in known:
            additions.append(candidate)
            known.add(key)
            if len(additions) == missing:
                break
    return additions


def component_support_for(components, count, general_status, rng):
    """Choose explicit support links, including a contrast when possible."""
    selected = rng.sample(components, count)
    statuses = ("supported", "partial", "unsupported", "planned")
    links = []
    for index, component in enumerate(selected):
        status = general_status if index == 0 else rng.choice(statuses)
        links.append({"component_id": component["id"], "support_status": status})
    if count > 1 and all(link["support_status"] == general_status for link in links):
        links[-1]["support_status"] = next(status for status in statuses if status != general_status)
    return links
