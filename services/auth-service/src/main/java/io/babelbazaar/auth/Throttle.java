package io.babelbazaar.auth;

import java.time.Clock;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.concurrent.ConcurrentHashMap;

/** A per-key sliding one-minute window. In memory: abuse control for the privileged doors, not a quota system. */
public class Throttle {

    private final int perMinute;
    private final Clock clock;
    private final ConcurrentHashMap<String, Deque<Long>> hits = new ConcurrentHashMap<>();

    public Throttle(int perMinute, Clock clock) {
        this.perMinute = perMinute;
        this.clock = clock;
    }

    /** Records a hit and says whether it is within the limit. */
    public boolean allow(String key) {
        long now = clock.millis();
        if (hits.size() > 50_000) {
            hits.entrySet().removeIf(e -> e.getValue().isEmpty() || now - e.getValue().peekLast() > 60_000);
        }
        Deque<Long> window = hits.computeIfAbsent(key, k -> new ArrayDeque<>());
        synchronized (window) {
            while (!window.isEmpty() && now - window.peekFirst() > 60_000) {
                window.pollFirst();
            }
            if (window.size() >= perMinute) {
                return false;
            }
            window.addLast(now);
            return true;
        }
    }
}
