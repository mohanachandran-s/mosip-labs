---
topic: framework/dsl-testrig
scope: framework
status: Verified (layout, run chain, report format); Inferred where marked
evidence: mosip/mosip-automation-tests develop @ a91fb1d and release-1.6.x; MAT-ONBOARD and MAT-JAVA25 task evidence
---

# DSL TestRig (mosip-automation-tests)

End-to-end scenarios that drive a real MOSIP environment: create users, centres and machines, build registration
packets through a local **packet creator** service, upload them and follow them to a UIN. A run needs three things up
at once: the deployed environment, the packet creator on this machine, and the orchestrator jar.

## Layout (verified)

| Path | What it is |
|---|---|
| `mosipTestDataProvider/` | persona / test-data library; build it before the packet creator |
| `mosip-packet-creator/` | Spring Boot service `dslrig-packetcreator-<ver>.jar`, context `/v1/packetcreator`, port 8080 |
| `mosip-packet-creator/src/main/resources/dockersupport/centralized/mosip-packet-creator/` | the run folder to copy: `config/application.properties` (blank credentials, tracked), `resource/`, `profile_resource/`, `Biometric Devices/`; its `.bat` scripts hold someone's absolute paths: do not run them |
| `mosip-acceptance-tests/` (ivv-core, ivv-parser, ivv-dg, ivv-orchestrator) | the DSL engine; only `ivv-orchestrator` is runnable: `dslrig-ivv-orchestrator-<ver>-jar-with-dependencies.jar`, main `io.mosip.testrig.dslrig.ivv.orchestrator.TestRunner` |
| `ivv-orchestrator/src/main/resources/config/scenarios.feature` | the scenarios in English Gherkin (`useGherkinScenarios=yes`), tagged `@scenario_<n>`; `Scenario_0` = before-suite setup (users, centres, machines), `Scenario_AFTER_SUITE` = teardown |
| `ivv-orchestrator/src/main/resources/config/dsl.properties` | run selection, endpoints **and secrets** (admin, keycloak, client secrets, DB password), read from the classpath (`dslConfigManager`: `<global resource path>/config/dsl.properties`) |
| `ivv-parser` | maps a readable step to a DSL method (`ReadableDslStepCodec`); its unit tests are the only fast tests |
| `ivv-orchestrator/src/main/java/.../ivv/e2e/methods/` | one class per step verb (`User`, `Center`, `Machine`, ...); reuse one before adding a step |

`apitest-commons` (mosip-functional-tests) is a dependency: `KernelAuthentication`, `BaseTestCase` and their
`Kernel.properties` come from there (repo README, step 3). *Inferred:* a branch that changes the commons version needs
that commons installed in `~/.m2` before the orchestrator build.

## Run chain (verified against the repo's AGENTS.md and the MAT-JAVA25 runs on dev2)

`<wt>` = the task worktree, `<task>` = `.crewline-work/tasks/<id>`, `<svc>` = `<task>/services/packetcreator`.
Each step is one Crewline command, so every step leaves a record.

| # | Step | Command |
|---|---|---|
| 1 | Build commons when the orchestrator needs a newer one | `crewline run --repo <commons repo> --stall 10 --out <task>/evidence/build-commons.json -- mvn -B clean install -Dgpg.skip=true -Dmaven.gitcommitid.skip=true` |
| 2 | Build data provider, packet creator, orchestrator | `crewline run --repo <wt> --stall 10 --out <task>/evidence/build-dsl.json -- mvn -B clean install -Dgpg.skip -DskipTests` (root reactor) |
| 3 | Copy the run folder and fill its config | `crewline service start ... --copy-from` copies it on first use; to fill it before the start, copy once (`cp -r <centralized>/mosip-packet-creator <svc>`), then `crewline creds merge --from <user values file> --into <svc>/config/application.properties`. Keys the user supplies (names only, never print values): `mosip.test.baseurl`, `mosip.test.regclient.machineid`, `.centerid`, `.userid`, `.password`, `.supervisorid`, `.supervisorpwd`, `.clientid`, `.secretkey`, `mosip.test.p12.secret` |
| 4 | Start the packet creator, wait until it answers | `crewline service start --name packetcreator --cwd <svc> --ready-url http://localhost:8080/v1/packetcreator/swagger-ui.html --ready-timeout 10 --out <task>/evidence/service-packetcreator.json -- java -Xss8m -Dfile.encoding=UTF-8 -jar <wt>/mosip-packet-creator/target/dslrig-packetcreator-<ver>.jar --spring.config.location=file:./config/application.properties` |
| 5 | Orchestrator config | `crewline creds merge --from <values> --into <wt>/mosip-acceptance-tests/ivv-orchestrator/src/main/resources/config/dsl.properties`: the secrets, plus `push-reports-to-s3=no` (develop ships `yes`, which uploads to S3), `packetUtilityBaseUrl=http://localhost:8080/v1/packetcreator`, `scenariosToExecute=` (empty = full suite) or the scenario numbers. `mountPath` is where packets go (`../mountvolume`, relative to the run folder); `mountPathForScenario` is read only with `useExternalScenarioSheet=yes` |
| 6 | Rebuild the orchestrator | the config is packaged from the classpath, so after every config change: `crewline run --repo <wt> --stall 10 --out <task>/evidence/build-orchestrator.json -- mvn -B -f mosip-acceptance-tests/pom.xml install -Dgpg.skip -DskipTests` |
| 7 | Health right before the run | `crewline service health --name packetcreator` |
| 8 | Run | `crewline run --repo <wt> --cwd <wt> --stall 20 --fail-on 'HTTP 401|NotAuthorizedException' --report <wt>/testng-report --out <task>/evidence/system-tests-dsl.json -- java -Denv.user=<env> -Denv.endpoint=<url> -jar mosip-acceptance-tests/ivv-orchestrator/target/dslrig-ivv-orchestrator-<ver>-jar-with-dependencies.jar` (`-D` before `-jar`; a full suite runs 8 threads, `maxSuiteTime=2` hours) |
| 9 | Stop, restore | `crewline service stop --name packetcreator --out <task>/evidence/service-packetcreator-stop.json`; `crewline creds restore --into <dsl.properties>` |
| 10 | Base vs branch | steps 2-9 on the base worktree (same env, same `scenariosToExecute`, one after the other), then `crewline regression --baseline <task>/evidence/reports/system-tests-dsl-base --candidate <task>/evidence/reports/system-tests-dsl --out <task>/evidence/full-regression-dsl.json` |

## Reports (verified on real files)

- `testng-report/` under the run folder keeps **every** run; `crewline run` copies only the files newer than the run.
- `DSL-<env>-<level>-run-<epoch ms>-report_T-<total>_P-<pass>_KI-<known>_I-<ignored>_S-<skip>_F-<fail>.html` is the
  full report. `crewline regression` reads it: each scenario is `data-name="Scenario_<n>" data-status="pass|fail|known|skip"`
  (1.3/1.4 reports: a `<tr class="passedeven|knownissueeven|...">` row). Known issue = FAIL, ignored = left out.
  The counts in the name are a quick check of the parse.
- `DSL-...-error-<epoch>-report_T-..._F-<n>.html` (failures only) and `...-report.html` without counts (the copy before
  the rename) are not full reports.
- Logs: `ivv-orchestrator/src/logs/mosip-api-test.log`; packet creator: `<svc>/crewline-service-packetcreator.log`.
- When `Scenario_0` (before-suite setup) fails, almost everything after it is skipped: read that scenario first.

## Known failure signatures (MAT-JAVA25: JDK 25, Spring Boot 4)

| Signature | Meaning |
|---|---|
| `NoClassDefFoundError: com/auth0/jwt/...` at packet creator start | `java-jwt` missing from the runtime classpath |
| `NullPointerException` in Groovy `ClosureMetaClass` | Groovy 5 incompatibility (fixed with groovy 4.0.33) |
| `/actuator/health` 404 | actuator not on the classpath: use the swagger-ui URL as the ready URL |
| `BaseUncheckedException` from kernel-core | kernel-core version mismatch |
| 401 / `NotAuthorizedException` at the first API call | wrong secrets in `dsl.properties` or commons `Kernel.properties`: stopped by `--fail-on`; ask the user |

## Rules

- Credentials go into tracked files only through `creds merge` (skip-worktree, restore afterwards); never commit
  `dsl.properties` or `application.properties` with values. The repo's AGENTS.md says the same.
- Never run against production; the environment and its credentials come from the user.
- PRs in this repo target `master` (AGENTS.md); confirm with `gh repo view mosip/mosip-automation-tests --json defaultBranchRef`.

## Unknown

- Whether CI runs any DSL scenario (the workflows build and publish images only).
- Whether one packet creator can serve a base and a branch run at once: run them one after the other.
