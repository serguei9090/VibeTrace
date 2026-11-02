#!/usr/bin/env bash

# Shell sample containing common Bash pitfalls and security smells
DIR="/tmp/test_dir"

# Triggers SC2164 / Sonar shell:S2164 (cd without error check)
cd $DIR

# Triggers SC2086 / Sonar shell:S2086 (unquoted variable expansion)
echo $DIR
rm -rf $DIR/*

# Triggers SC2046 / Sonar shell:S2046 (unquoted command substitution)
for file in $(ls *.log); do
    # Triggers SC2002 / Sonar shell:S2002 (useless cat)
    cat $file | grep "ERROR"
done
