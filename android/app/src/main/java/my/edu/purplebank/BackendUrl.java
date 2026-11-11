package my.edu.purplebank;

import java.net.URI;

/** Shared validation for the banking client and the independent PIN exercise. */
public final class BackendUrl {
    private BackendUrl() {}
    public static String normalise(String value, boolean debug) throws Exception {
        URI uri = new URI(value.trim());
        if (uri.getHost() == null || uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null
                || !("https".equals(uri.getScheme()) || debug && "http".equals(uri.getScheme()))
                || uri.getPort() > 65535 || uri.getPort() == 0)
            throw new IllegalArgumentException("Use an HTTPS backend URL; HTTP is debug-only. No credentials, query or fragment.");
        return uri.toString().replaceAll("/+$", "");
    }
}
