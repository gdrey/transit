# Modeling policy

- Scope changes using affected routes, directions, locations, active periods and service-day times, including times after 24:00. Include trips already underway when they reach an affected location during a window; apply the change to the relevant whole trip.
- Preserve the original feed. Use date-scoped service and trip variants; combine compatible operations on one occurrence. Flag incompatible operations and do not silently choose a winner.
- Stops can be represented as no pickup and no drop-off, or removed, depending on the intended semantics. Show the chosen representation in comparison and verify the exported records.
- For added stops or reroutes, use existing compatible route/trip segments for stop order, times and shapes where possible. Mark estimated timing or geometry clearly. Flag missing reliable evidence. Update `shapes.txt` and dependent references when present and affected; do not claim unverified geometry is correct.
- For frequency changes, interpret the relevant location and active period in context. Anchor stated intervals to the period when appropriate, and examine the transition to adjacent scheduled service. A frequency writer is not implemented yet.
- Replacement buses can be drafted as estimated static bus service with provisional stops, approximate rail-based timing and straight-line shapes where warranted. Label estimates and absence of real-time data; a replacement-bus writer is not implemented yet.
- Use published active periods when wording is less precise, preserving source timestamps and recording material assumptions. Minor rounding differences alone do not establish a conflict. An alert's active period is not by itself proof that a schedule change is present.
- Obvious facility-only notices and current disruptions without a relevant future schedule change generally do not require static schedule edits.
- Route metadata edits use GTFS fields, including `routes.txt` `route_type=3` for bus, with trolleybus type 11 distinct; use actual route IDs from the loaded feed.
