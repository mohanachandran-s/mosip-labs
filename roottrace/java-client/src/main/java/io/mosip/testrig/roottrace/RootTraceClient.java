package io.mosip.testrig.roottrace;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;

/** Zero-dependency client for the roottrace HTTP API. Base URL from -DrootTraceUrl or ROOTTRACE_URL. */
public final class RootTraceClient {
    private final String baseUrl;
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(10)).build();

    public RootTraceClient(String baseUrl) { this.baseUrl = baseUrl.replaceAll("/+$", ""); }

    public static RootTraceClient fromEnv() {
        String url = System.getProperty("rootTraceUrl", System.getenv("ROOTTRACE_URL"));
        return (url == null || url.isBlank()) ? null : new RootTraceClient(url);
    }

    /** Full health scan of a time window (e.g. the test run). Returns raw JSON. */
    public String scan(Instant from, Instant to, String namespacesCsv) throws Exception {
        String q = "from=" + from + "&to=" + to + (namespacesCsv == null ? "" : "&namespace=" + enc(namespacesCsv));
        return get("/scan?" + q, Duration.ofMinutes(10));
    }

    /** Every backend log line for a transaction/trace id, across services, in time order. */
    public String trace(String id, Instant from, Instant to) throws Exception {
        return get("/trace/" + enc(id) + "?from=" + from + "&to=" + to, Duration.ofMinutes(2));
    }

    public String reportUrl() { return baseUrl + "/report"; }

    private String get(String path, Duration timeout) throws Exception {
        HttpRequest req = HttpRequest.newBuilder(URI.create(baseUrl + path)).timeout(timeout).GET().build();
        HttpResponse<String> r = http.send(req, HttpResponse.BodyHandlers.ofString());
        if (r.statusCode() >= 400) throw new IllegalStateException("roottrace " + r.statusCode() + ": " + r.body());
        return r.body();
    }

    /** Tiny extractor so callers don't need a JSON library for the headline fields. */
    public static String field(String json, String name) {
        var m = java.util.regex.Pattern.compile("\"" + name + "\"\\s*:\\s*\"((?:[^\"\\\\]|\\\\.)*)\"").matcher(json);
        return m.find() ? m.group(1) : null;
    }

    private static String enc(String s) { return URLEncoder.encode(s, StandardCharsets.UTF_8); }
}
