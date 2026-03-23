#!/bin/sh
set -e

PIDS=""
for i in 0 1 2 3 4 5; do
    PORT=$((9050 + i))
    CPORT=$((9150 + i))
    mkdir -p /var/lib/tor/data$i
    chmod 700 /var/lib/tor/data$i
    chown -R tor:tor /var/lib/tor/data$i 2>/dev/null || echo "[start-tor] Warning: could not chown /var/lib/tor/data$i (continuing)"
    sed "s/PORT/$PORT/g; s/CPORT/$CPORT/g; s/IDX/$i/g" /etc/tor/torrc.template > /etc/tor/torrc$i
    tor -f /etc/tor/torrc$i &
    PID=$!
    echo "[start-tor] Tor instance $i started (PID=$PID, SocksPort=$PORT, ControlPort=$CPORT)"
    PIDS="$PIDS $PID"
    sleep 2
done

echo "[start-tor] All Tor instances launched. PIDs:$PIDS"
echo "[start-tor] Monitoring Tor processes..."

# Monitor loop: keep container alive and restart crashed Tor instances
while true; do
    ALL_DEAD=true
    for PID in $PIDS; do
        if kill -0 $PID 2>/dev/null; then
            ALL_DEAD=false
        fi
    done
    if $ALL_DEAD; then
        echo "[start-tor] ERROR: All Tor processes have exited! Restarting..."
        PIDS=""
        for i in 0 1 2 3 4 5; do
            PORT=$((9050 + i))
            CPORT=$((9150 + i))
            tor -f /etc/tor/torrc$i &
            PID=$!
            PIDS="$PIDS $PID"
            echo "[start-tor] Restarted Tor instance $i (PID=$PID, SocksPort=$PORT, ControlPort=$CPORT)"
            sleep 2
        done
    fi
    sleep 10
done
