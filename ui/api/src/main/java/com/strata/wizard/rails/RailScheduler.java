package com.strata.wizard.rails;

import org.springframework.stereotype.Component;

import java.time.*;
import java.time.format.DateTimeFormatter;
import java.util.Map;

/**
 * Rail availability + cut-off logic.
 *
 * Given a rail's {@code schedule} block from the registry and a {@link Clock},
 * answers: can this rail accept a payment RIGHT NOW, when does the current
 * window end, when does the next window open, and when will the funds settle.
 *
 * Intentionally simple — no holiday calendar. Weekend handling is folded in
 * via the rail's {@code weekend_open} flag.
 */
@Component
public class RailScheduler {

    private static final DateTimeFormatter TIME_FMT = DateTimeFormatter.ofPattern("HH:mm");

    /** Result of a single scheduler query. */
    public record Availability(
            boolean available_now,
            String status_text,        // human-readable, used directly in the UI
            String urgency,            // "now" | "today_soon" | "today" | "next_window"
            ZonedDateTime cutoff_at,   // null if 24x7
            ZonedDateTime next_window_at,
            ZonedDateTime settles_by
    ) {}

    /**
     * Compute availability for a rail at a moment in time.
     *
     * @param schedule the rail's `schedule` block from the registry
     * @param clock    test-overridable clock; pass {@link Clock#systemDefaultZone()}
     *                 in production
     */
    @SuppressWarnings("unchecked")
    public Availability availability(Map<String, Object> schedule, Clock clock) {
        if (schedule == null) {
            // Conservative default — treat as 24x7 instant if unknown.
            ZonedDateTime now = ZonedDateTime.now(clock);
            return new Availability(true, "Available now", "now", null, now, now);
        }

        boolean operates24x7 = Boolean.TRUE.equals(schedule.get("operates_24x7"));
        boolean weekendOpen = Boolean.TRUE.equals(schedule.get("weekend_open"));
        String tzId = (String) schedule.getOrDefault("timezone", "UTC");
        ZoneId tz = ZoneId.of(tzId);
        ZonedDateTime now = ZonedDateTime.now(clock).withZoneSameInstant(tz);
        int settlementOffsetDays = ((Number) schedule.getOrDefault("settlement_offset_days", 0)).intValue();

        // ── 24x7 instant rails — easy path ──────────────────────────── //
        if (operates24x7 && weekendOpen) {
            return new Availability(
                    true,
                    "Available now · 24/7",
                    "now",
                    null,
                    now,
                    now
            );
        }

        // ── Business-hours rails ────────────────────────────────────── //
        String cutoffStr = (String) schedule.get("cutoff_time");
        LocalTime cutoff = cutoffStr != null ? LocalTime.parse(cutoffStr) : LocalTime.of(17, 0);
        ZonedDateTime cutoffToday = now.with(cutoff);

        boolean isWeekendToday = isWeekend(now.toLocalDate());
        boolean isOperatingToday = !isWeekendToday || weekendOpen;
        boolean beforeCutoff = now.isBefore(cutoffToday);

        if (isOperatingToday && beforeCutoff) {
            Duration untilCutoff = Duration.between(now, cutoffToday);
            String urgency = untilCutoff.toMinutes() <= 60 ? "today_soon" : "today";
            String label = urgency.equals("today_soon")
                    ? "Cuts off in " + humanizeDuration(untilCutoff) + " (" + cutoffToday.format(TIME_FMT) + " " + tzShort(tz) + ")"
                    : "Cut-off " + cutoffToday.format(TIME_FMT) + " " + tzShort(tz);
            ZonedDateTime settlesBy = addBusinessDays(cutoffToday, settlementOffsetDays);
            return new Availability(true, label, urgency, cutoffToday, cutoffToday, settlesBy);
        }

        // After cutoff or weekend — find next operating window
        ZonedDateTime nextOpen = nextOperatingDay(now.toLocalDate(), weekendOpen).atTime(LocalTime.of(9, 0)).atZone(tz);
        ZonedDateTime settlesBy = addBusinessDays(nextOpen, settlementOffsetDays);
        String label;
        if (isWeekendToday && !weekendOpen) {
            label = "Closed weekends · next " + nextOpen.format(DateTimeFormatter.ofPattern("EEE HH:mm")) + " " + tzShort(tz);
        } else {
            label = "After cut-off · next " + nextOpen.format(DateTimeFormatter.ofPattern("EEE HH:mm")) + " " + tzShort(tz);
        }
        return new Availability(false, label, "next_window", cutoffToday, nextOpen, settlesBy);
    }

    /** First date on/after `from` that's an operating day. */
    private static LocalDate nextOperatingDay(LocalDate from, boolean weekendOpen) {
        LocalDate d = from.plusDays(1);
        while (!weekendOpen && isWeekend(d)) {
            d = d.plusDays(1);
        }
        return d;
    }

    /** Add N business days, skipping Sat+Sun. */
    private static ZonedDateTime addBusinessDays(ZonedDateTime start, int days) {
        ZonedDateTime d = start;
        int added = 0;
        while (added < days) {
            d = d.plusDays(1);
            if (!isWeekend(d.toLocalDate())) added++;
        }
        return d;
    }

    private static boolean isWeekend(LocalDate d) {
        DayOfWeek dow = d.getDayOfWeek();
        return dow == DayOfWeek.SATURDAY || dow == DayOfWeek.SUNDAY;
    }

    /** Compact "2h 15m" / "45m" / "12s" formatting for "cuts off in X" labels. */
    private static String humanizeDuration(Duration d) {
        long h = d.toHours();
        long m = d.minusHours(h).toMinutes();
        if (h > 0) return h + "h " + m + "m";
        if (m > 0) return m + "m";
        return d.toSeconds() + "s";
    }

    /** Best-effort short tz name (NYC, LON, BLR, GMT, …). */
    private static String tzShort(ZoneId tz) {
        String id = tz.getId();
        return switch (id) {
            case "America/New_York" -> "ET";
            case "Europe/London"     -> "London";
            case "Europe/Brussels"   -> "CET";
            case "Asia/Kolkata"      -> "IST";
            case "America/Sao_Paulo" -> "BRT";
            default                   -> id.contains("/") ? id.substring(id.lastIndexOf('/') + 1) : id;
        };
    }
}
