package identity

import (
	"reflect"
	"sort"
	"testing"
)

func TestServiceAccountNamesSortedAndComplete(t *testing.T) {
	got := ServiceAccountNames()
	if !sort.StringsAreSorted(got) {
		t.Errorf("not sorted: %v", got)
	}
	if len(got) != len(serviceAccounts) {
		t.Fatalf("got %d names, want %d", len(got), len(serviceAccounts))
	}
	for _, n := range got {
		if !serviceAccounts[n] {
			t.Errorf("%q is not a harness account", n)
		}
	}
	if !reflect.DeepEqual(got, ServiceAccountNames()) {
		t.Error("not deterministic")
	}
}
