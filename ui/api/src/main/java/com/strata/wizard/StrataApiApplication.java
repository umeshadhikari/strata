package com.strata.wizard;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * Entry point — mirrors the FastAPI app description and registers the
 * three router groups (tables, payments, wizard) via component scanning.
 */
@SpringBootApplication
public class StrataApiApplication {
    public static void main(String[] args) {
        SpringApplication.run(StrataApiApplication.class, args);
    }
}
