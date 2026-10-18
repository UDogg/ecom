// Command loadgen is a small, dependency-free HTTP load generator for the
// benchmarks in bench/ (README section 17): fixed concurrency for a fixed
// duration, reporting throughput and latency percentiles.
//
//	go run ./bench/loadgen -c 64 -d 20s http://localhost:8080/api/catalog/products
package main

import (
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"sort"
	"sync"
	"sync/atomic"
	"time"
)

func main() {
	conc := flag.Int("c", 32, "concurrent connections")
	dur := flag.Duration("d", 15*time.Second, "duration")
	warm := flag.Duration("w", 3*time.Second, "warm-up (not measured)")
	flag.Parse()
	if flag.NArg() != 1 {
		fmt.Fprintln(os.Stderr, "usage: loadgen [-c N] [-d 15s] URL")
		os.Exit(2)
	}
	url := flag.Arg(0)
	client := &http.Client{Timeout: 10 * time.Second, Transport: &http.Transport{
		MaxIdleConnsPerHost: *conc * 2, MaxConnsPerHost: *conc * 2, DisableCompression: false}}

	var ok, failed atomic.Int64
	var mu sync.Mutex
	var lat []time.Duration
	measuring := atomic.Bool{}
	stop := time.Now().Add(*warm + *dur)
	go func() { time.Sleep(*warm); measuring.Store(true) }()

	var wg sync.WaitGroup
	for i := 0; i < *conc; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			local := make([]time.Duration, 0, 4096)
			for time.Now().Before(stop) {
				t0 := time.Now()
				resp, err := client.Get(url)
				if err == nil {
					io.Copy(io.Discard, resp.Body)
					resp.Body.Close()
				}
				if !measuring.Load() {
					continue
				}
				if err != nil || resp.StatusCode >= 400 {
					failed.Add(1)
					continue
				}
				ok.Add(1)
				local = append(local, time.Since(t0))
			}
			mu.Lock()
			lat = append(lat, local...)
			mu.Unlock()
		}()
	}
	wg.Wait()
	sort.Slice(lat, func(i, j int) bool { return lat[i] < lat[j] })
	pct := func(p float64) time.Duration {
		if len(lat) == 0 {
			return 0
		}
		return lat[int(float64(len(lat)-1)*p)]
	}
	fmt.Printf("%s  c=%d  %s\n", url, *conc, *dur)
	fmt.Printf("  ok %d  failed %d  rps %.0f\n", ok.Load(), failed.Load(), float64(ok.Load())/dur.Seconds())
	fmt.Printf("  p50 %v  p95 %v  p99 %v  max %v\n", pct(.50).Round(10*time.Microsecond), pct(.95).Round(10*time.Microsecond),
		pct(.99).Round(10*time.Microsecond), pct(1).Round(10*time.Microsecond))
}
