package com.strata.wizard.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.time.Clock;

/**
 * Single Clock bean so rail-scheduler logic is testable (override in tests
 * with a {@link Clock#fixed} instance) and uses one consistent wall-clock
 * source in production.
 */
@Configuration
public class ClockConfig {

    @Bean
    public Clock systemClock() {
        return Clock.systemDefaultZone();
    }
}
