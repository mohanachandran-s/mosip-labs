package io.mosip.testrig.roottrace;

import java.time.Instant;
import org.testng.ISuite;
import org.testng.ISuiteListener;
import org.testng.ITestListener;
import org.testng.ITestResult;
import org.testng.Reporter;

/**
 * TestNG listener: on each failure, pulls the backend story for that test from roottrace;
 * at suite end, runs a health scan of the whole run window.
 *
 * Enable with  -DrootTraceUrl=http://roottrace.roottrace.svc  and register in testng.xml:
 *   <listeners><listener class-name="io.mosip.testrig.roottrace.RootTraceListener"/></listeners>
 * Tests can set result.setAttribute("transactionId", txnId) for a precise trace;
 * otherwise the test's own time window (±60s) is scanned.
 */
public class RootTraceListener implements ITestListener, ISuiteListener {
    private final RootTraceClient client = RootTraceClient.fromEnv();
    private Instant suiteStart;

    @Override public void onStart(ISuite suite) { suiteStart = Instant.now(); }

    @Override
    public void onTestFailure(ITestResult result) {
        if (client == null) return;
        Instant from = Instant.ofEpochMilli(result.getStartMillis()).minusSeconds(60);
        Instant to = Instant.ofEpochMilli(result.getEndMillis()).plusSeconds(60);
        try {
            Object txn = result.getAttribute("transactionId");
            String headline;
            if (txn != null) {
                String json = client.trace(txn.toString(), from, to);
                boolean noError = json.matches("(?s).*\"first_error\"\\s*:\\s*null.*");
                String svc = RootTraceClient.field(json, "service");
                String msg = RootTraceClient.field(json, "message");
                headline = noError || svc == null ? "no backend error found for " + txn
                        : "first backend error in " + svc + ": " + msg;
                result.setAttribute("rootTraceTrace", json);
            } else {
                String json = client.scan(from, to, null);
                headline = RootTraceClient.field(json, "verdict");
                result.setAttribute("rootTraceScan", json);
            }
            result.setAttribute("rootTraceHeadline", headline);
            Reporter.log("[roottrace] " + headline, true);
        } catch (Exception e) {
            Reporter.log("[roottrace] unavailable: " + e.getMessage(), true);
        }
    }

    @Override
    public void onFinish(ISuite suite) {
        if (client == null || suiteStart == null) return;
        try {
            String json = client.scan(suiteStart, Instant.now(), null);
            System.out.println("[roottrace] run health: " + RootTraceClient.field(json, "verdict")
                    + "  ->  " + client.reportUrl());
        } catch (Exception e) {
            System.out.println("[roottrace] unavailable: " + e.getMessage());
        }
    }
}
