package com.strata.wizard.wizard;

import com.strata.wizard.rails.*;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;

import java.util.*;

/**
 * REST surface — identical paths and response shapes to the FastAPI wizard
 * router, so the existing Angular ApiService talks to either backend without
 * any frontend change.
 */
@RestController
@RequestMapping("/api/wizard")
public class WizardController {

    private final RailsRegistry registry;
    private final Directory directory;
    private final Lookups lookups;
    private final Selector selector;
    private final WizardService service;

    public WizardController(RailsRegistry registry, Directory directory, Lookups lookups,
                            Selector selector, WizardService service) {
        this.registry = registry;
        this.directory = directory;
        this.lookups = lookups;
        this.selector = selector;
        this.service = service;
    }

    @GetMapping("/rails")
    public Map<String, Object> listRails() {
        return registry.asMap();
    }

    @GetMapping("/accounts")
    public Map<String, Object> listAccounts(@RequestParam(required = false) String currency,
                                            @RequestParam(required = false) String q) {
        return Map.of("accounts", directory.findAccounts(currency, q));
    }

    @GetMapping("/beneficiaries")
    public Map<String, Object> listBeneficiaries(@RequestParam(required = false) String q,
                                                 @RequestParam(required = false) String country) {
        var results = new ArrayList<>(directory.findBeneficiaries(q, country));
        results.sort(Comparator.comparing(b -> (String) b.get("name")));
        return Map.of("beneficiaries", results);
    }

    @GetMapping("/beneficiaries/{id}")
    public Map<String, Object> getBeneficiary(@PathVariable int id) {
        Map<String, Object> b = directory.getBeneficiary(id);
        if (b == null) throw new ResponseStatusException(HttpStatus.NOT_FOUND, "Beneficiary not found");
        return b;
    }

    @GetMapping("/iban-lookup")
    public Map<String, Object> ibanLookup(@RequestParam String iban) {
        Lookups.Bank bank = lookups.ibanToBank(iban);
        return Map.of(
                "iban", iban,
                "bank", bank == null ? Map.of() : Map.of("bic", bank.bic(), "name", bank.name())
        );
    }

    public record SelectRailsRequest(String country, String currency, Double amount, String urgency) {}

    @PostMapping("/select-rail")
    public Map<String, Object> selectRail(@RequestBody SelectRailsRequest req) {
        return Map.of("candidates", selector.selectRails(
                req.country(), req.currency(), req.amount(), req.urgency()));
    }

    @PostMapping("/turn")
    public TurnResponse turn(@RequestBody TurnRequest req) {
        return service.turn(req);
    }
}
