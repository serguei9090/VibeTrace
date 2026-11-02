package main

import (
	"fmt"
	"os"
)

// unusedHelper is an uncalled private method (Sonar go:S1144 / deadcode).
func unusedHelper() string {
	return "dead code"
}

// ComplexEvaluator triggers high cyclomatic complexity (Sonar go:S3776).
func ComplexEvaluator(a, b, c int, mode string) int {
	result := 0
	if a > 10 {
		if b > 5 {
			for i := 0; i < 10; i++ {
				if c == i {
					result += i * 2
				} else if c > i {
					result += i
				}
			}
		} else {
			result -= 1
		}
	} else if a < 0 {
		if mode == "fast" {
			result = -10
		} else if mode == "slow" {
			result = -20
		} else {
			result = -30
		}
	}
	return result
}

func main() {
	// Unhandled error return (Sonar go:S112 / errcheck)
	os.Setenv("VIBETRACE_TEST", "1")
	val := ComplexEvaluator(15, 10, 5, "standard")
	fmt.Printf("Evaluation result: %d\n", val)
}
