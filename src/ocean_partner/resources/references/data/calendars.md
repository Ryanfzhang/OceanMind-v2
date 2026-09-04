# Calendars

CF calendars such as `360_day`, `noleap`, and `all_leap` cannot be silently converted through ordinary JavaScript dates. Preserve numeric offsets, units, and calendar when a standard Gregorian representation is unavailable. Define monthly and seasonal aggregation rules explicitly around incomplete periods and leap days.
