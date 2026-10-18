package session

import (
	"testing"
	"time"
)

type clock struct{ t time.Time }

func store(max int) (*Store, *clock) {
	c := &clock{time.Unix(1_800_000_000, 0)}
	st := NewStore(30*time.Minute, 8*time.Hour, max)
	st.Now = func() time.Time { return c.t }
	return st, c
}

func TestIdleAndAbsoluteExpiry(t *testing.T) {
	st, c := store(10)
	id, _, _ := st.New()
	c.t = c.t.Add(29 * time.Minute)
	if _, ok := st.Get(id); !ok {
		t.Fatal("expired too early")
	}
	c.t = c.t.Add(31 * time.Minute)
	if _, ok := st.Get(id); ok {
		t.Fatal("idle session survived")
	}

	id, _, _ = st.New()
	for i := 0; i < 20; i++ { // keep it busy, past the 8 h lifetime
		c.t = c.t.Add(25 * time.Minute)
		st.Get(id)
	}
	if _, ok := st.Get(id); ok {
		t.Fatal("session outlived its absolute lifetime")
	}
}

func TestRotateInvalidatesTheOldIDAndCSRF(t *testing.T) {
	st, _ := store(10)
	oldID, s, _ := st.New()
	oldCSRF := s.CSRF
	newID, s2, ok := st.Rotate(oldID)
	if !ok || newID == oldID || s2.CSRF == oldCSRF {
		t.Fatal("rotation kept the id or the CSRF token")
	}
	if _, ok := st.Get(oldID); ok {
		t.Fatal("the pre-login id still works (session fixation)")
	}
	if _, ok := st.Get(newID); !ok {
		t.Fatal("new id does not work")
	}
}

func TestStoreNeverHoldsTheCookieValue(t *testing.T) {
	st, _ := store(10)
	id, _, _ := st.New()
	for k := range st.sessions {
		if string(k[:]) == id {
			t.Fatal("raw id used as key")
		}
	}
}

func TestFullStoreRefusesButSweepsFirst(t *testing.T) {
	st, c := store(2)
	st.New()
	st.New()
	if _, _, err := st.New(); err != ErrFull {
		t.Fatalf("got %v, want ErrFull", err)
	}
	c.t = c.t.Add(time.Hour)
	if _, _, err := st.New(); err != nil {
		t.Fatalf("expired sessions were not swept: %v", err)
	}
}

func TestCSRFCheck(t *testing.T) {
	st, _ := store(1)
	_, s, _ := st.New()
	if !s.CheckCSRF(s.CSRF) || s.CheckCSRF("") || s.CheckCSRF(s.CSRF+"x") {
		t.Fatal("CSRF comparison is wrong")
	}
}

func TestOversizedIDIsIgnored(t *testing.T) {
	st, _ := store(1)
	if _, ok := st.Get(string(make([]byte, 4096))); ok {
		t.Fatal("accepted absurd id")
	}
}
