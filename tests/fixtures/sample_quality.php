<?php

class SampleQuality {
    private function unusedPrivateMethod() {
        return "never called";
    }

    public function calculateScore($a, $b, $c, $d, $e, $f, $g, $h) {
        $x = 10;
        $x = 20; // Dead store

        if ($a > 0) {
            if ($b > 0) { // Mergeable if
                for ($i = 0; $i < 10; $i++) {
                    if ($c > 5) {
                        while ($d > 0) {
                            if ($e) {
                                switch ($f) {
                                    case 1:
                                        echo "one";
                                        break;
                                    case 2:
                                        echo "two";
                                        break;
                                }
                            }
                            $d--;
                        }
                    }
                }
            }
        }

        $tier = $a ? ($b ? "gold" : "silver") : ($c ? "bronze" : "none"); // Nested ternary

        echo "status_active";
        echo "status_active";
        echo "status_active";
        echo "status_active";

        return; // Redundant return
    }
}
