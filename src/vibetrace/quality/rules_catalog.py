"""Canonical Quality Rules Catalog containing specifications for Sonar RSPEC rules.

Declarative definitions of rule specs, CWE mappings, descriptions, and code examples.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from vibetrace.quality.models import QualityCategory, QualitySeverity


@dataclass(frozen=True)
class QualityRuleSpec:
    """Specification of an AST quality/maintainability rule with explanatory guidance."""

    rule_id: str
    rspec_id: str
    severity: QualitySeverity
    category: QualityCategory
    title: str
    explanation: str
    non_compliant_example: str
    compliant_example: str
    default_threshold: int | None = None
    aliases: list[str] = field(default_factory=list)
    compliant_by_lang: dict[str, str] = field(default_factory=dict)
    cwe_ids: list[str] = field(default_factory=list)
    owasp: str | None = None

    def get_compliant_example(self, lang: str) -> str:
        return self.compliant_by_lang.get(lang, self.compliant_example)


QUALITY_RULES_CATALOG: dict[str, QualityRuleSpec] = {
    # 1. S3776: Cognitive Complexity
    "S3776": QualityRuleSpec(
        rule_id="S3776",
        rspec_id="RSPEC-3776",
        severity=QualitySeverity.HIGH,
        category=QualityCategory.COMPLEXITY,
        title="Cognitive Complexity of functions should not be too high",
        explanation=(
            "Cognitive Complexity is a measure of how hard the control flow is to understand. "
            "Each break in linear flow (loops, conditionals, catches, binary operators) adds to "
            "the score, with nested layers penalized increasingly. Functions with high cognitive "
            "complexity should be refactored into smaller, single-responsibility helper functions."
        ),
        default_threshold=15,
        aliases=["C901", "complexity", "cognitive-complexity"],
        non_compliant_example=(
            "def process(users):\n"
            "    for u in users:\n"
            "        if u.is_active:\n"
            "            if u.has_profile:\n"
            "                u.do_work()\n"
        ),
        compliant_example=(
            "def process(users):\n"
            "    for u in users:\n"
            "        if is_eligible(u):\n"
            "            u.do_work()\n"
        ),
    ),
    # 2. S1066: Mergeable "if" statements
    "S1066": QualityRuleSpec(
        rule_id="S1066",
        rspec_id="RSPEC-1066",
        severity=QualitySeverity.MEDIUM,
        category=QualityCategory.CODE_SMELL,
        title='Mergeable "if" statements should be combined',
        explanation=(
            "Nested 'if' statements with no intervening statements or 'else' blocks unnecessarily "
            "increase nesting. Merging them with 'and' flattens the code and improves readability."
        ),
        aliases=["SIM102", "collapsible-if"],
        non_compliant_example=(
            "if file.is_valid():\n    if file.is_file():\n        process(file)\n"
        ),
        compliant_example=("if file.is_valid() and file.is_file():\n    process(file)\n"),
    ),
    # 3. S134: Deep nesting of control flow
    "S134": QualityRuleSpec(
        rule_id="S134",
        rspec_id="RSPEC-134",
        severity=QualitySeverity.MEDIUM,
        category=QualityCategory.MAINTAINABILITY,
        title="Control flow statements should not be nested too deeply",
        explanation=(
            "Nesting control flow statements beyond 3 or 4 levels creates cognitive overload. "
            "Extract inner loops or conditionals into separate functions or invert conditions."
        ),
        default_threshold=3,
        aliases=["PLR0912", "max-nesting"],
        non_compliant_example=(
            "for a in items:\n"
            "    if a.ok:\n"
            "        for b in a.children:\n"
            "            if b.ready:\n"
            "                b.run()\n"
        ),
        compliant_example=(
            "for a in items:\n    if not a.ok:\n        continue\n    process_item(a)\n"
        ),
    ),
    # 4. S107: Too many parameters
    "S107": QualityRuleSpec(
        rule_id="S107",
        rspec_id="RSPEC-107",
        severity=QualitySeverity.LOW,
        category=QualityCategory.CODE_SMELL,
        title="Functions should not have too many parameters",
        explanation=(
            "A long parameter list indicates that a function has too many responsibilities or "
            "that parameters should be grouped into a dataclass or Pydantic model."
        ),
        default_threshold=7,
        aliases=["PLR0913", "too-many-arguments"],
        non_compliant_example=(
            "def create_user(name, email, age, address, phone, zip_code, country, role):\n"
            "    pass\n"
        ),
        compliant_example="def create_user(profile: UserProfile, role: str):\n    pass\n",
    ),
    # 5. S1186: Empty functions
    "S1186": QualityRuleSpec(
        rule_id="S1186",
        rspec_id="RSPEC-1186",
        severity=QualitySeverity.LOW,
        category=QualityCategory.CODE_SMELL,
        title="Methods and functions should not be empty without explanation",
        explanation=(
            "Empty functions either represent incomplete implementation or an unnecessary stub. "
            "If an empty body is intentional (e.g. an abstract hook), add a comment explaining why."
        ),
        aliases=["empty-function"],
        non_compliant_example="def on_event():\n    pass\n",
        compliant_example=(
            "def on_event():\n    # Intentionally empty: default no-op handler\n    pass\n"
        ),
    ),
    # 6. S5714 / B006: Mutable default argument
    "S5714": QualityRuleSpec(
        rule_id="S5714",
        rspec_id="RSPEC-5714",
        severity=QualitySeverity.HIGH,
        category=QualityCategory.RELIABILITY,
        title="Default argument values should not be mutable",
        explanation=(
            "Default parameter values are evaluated once when the function definition is executed, "
            "not each time called. Modifying a mutable default (like a list or dict) "
            "persists across all future invocations."
        ),
        aliases=["B006", "W0102", "mutable-default", "Google:MutableDefault"],
        non_compliant_example=(
            "def append_item(val, items=[]):\n    items.append(val)\n    return items\n"
        ),
        compliant_example=(
            "def append_item(val, items=None):\n"
            "    if items is None:\n"
            "        items = []\n"
            "    items.append(val)\n"
            "    return items\n"
        ),
    ),
    # 7. S1764 / Google:SelfComparison: Identical operands
    "S1764": QualityRuleSpec(
        rule_id="S1764",
        rspec_id="RSPEC-1764",
        severity=QualitySeverity.HIGH,
        category=QualityCategory.RELIABILITY,
        title="Identical expressions should not be on both sides of a binary operator",
        explanation=(
            "Comparing an expression to itself (e.g. `x == x` or `x != x`) or joining identical "
            "expressions with `and` or `or` is redundant and usually indicates a copy-paste bug."
        ),
        aliases=["B012", "Google:SelfComparison", "identical-operands"],
        non_compliant_example="if total == total:\n    pass\n",
        compliant_example="if current_total == expected_total:\n    pass\n",
    ),
    # 8. S1763 / Google:DeadCode: Unreachable code
    "S1763": QualityRuleSpec(
        rule_id="S1763",
        rspec_id="RSPEC-1763",
        severity=QualitySeverity.MEDIUM,
        category=QualityCategory.CODE_SMELL,
        title="Statements should not occur after terminal jump statements",
        explanation=(
            "Code placed immediately after `return`, `raise`, `break`, or `continue` in the same "
            "block will never be executed. Remove the dead statements to avoid confusion."
        ),
        aliases=["W0101", "unreachable-code", "Google:DeadCode"],
        non_compliant_example="def get_val():\n    return 42\n    print('done')\n",
        compliant_example="def get_val():\n    print('done')\n    return 42\n",
    ),
    # 9. S5712 / Google:DuplicateKey: Duplicate dictionary keys
    "S5712": QualityRuleSpec(
        rule_id="S5712",
        rspec_id="RSPEC-5712",
        severity=QualitySeverity.HIGH,
        category=QualityCategory.RELIABILITY,
        title="Dictionary literals should not contain duplicate keys",
        explanation=(
            "Defining duplicate keys in a dictionary literal silently overwrites previous values, "
            "which almost always indicates a merge conflict or copy-paste bug."
        ),
        aliases=["F601", "Google:DuplicateKey", "duplicate-key"],
        non_compliant_example="config = {'timeout': 30, 'retries': 3, 'timeout': 60}\n",
        compliant_example="config = {'timeout': 60, 'retries': 3}\n",
    ),
    # 10. S2757: Typo assignment operator
    "S2757": QualityRuleSpec(
        rule_id="S2757",
        rspec_id="RSPEC-2757",
        severity=QualitySeverity.HIGH,
        category=QualityCategory.RELIABILITY,
        title="'=-' or '=+' should not be used instead of '-=' or '+='",
        explanation=(
            "Writing `x =- 1` assigns negative 1 to `x` rather than decrementing it (`x -= 1`). "
            "Ensure the intended augmented assignment operator is used."
        ),
        aliases=["typo-assignment", "suspicious-assignment"],
        non_compliant_example="target_count =- 1\n",
        compliant_example="target_count -= 1\n",
    ),
    # 11. S9449: Transition should specify properties
    "S9449": QualityRuleSpec(
        rule_id="S9449",
        rspec_id="RSPEC-9449",
        severity=QualitySeverity.MEDIUM,
        category=QualityCategory.MAINTAINABILITY,
        title=(
            "Specify which properties should transition so future style changes do not "
            "animate unexpectedly"
        ),
        explanation=(
            "Using `transition: all` or omitting target property names causes the browser engine "
            "to transition all computed styles, including layout and repaint properties, leading "
            "to performance degradation and accidental visual artifacts."
        ),
        aliases=["transition-all", "css:transition-property"],
        non_compliant_example=".input { transition: all 0.2s ease; }\n",
        compliant_example=".input { transition: border-color 0.2s ease, box-shadow 0.2s ease; }\n",
    ),
    # 12. S9440: Respect prefers-reduced-motion
    "S9440": QualityRuleSpec(
        rule_id="S9440",
        rspec_id="RSPEC-9440",
        severity=QualitySeverity.HIGH,
        category=QualityCategory.ACCESSIBILITY,
        title="Respect reduced-motion preferences here to avoid triggering motion sensitivity",
        explanation=(
            "Animations and movement transitions can trigger vestibular motion sickness, vertigo, "
            "or disorientation. Guard continuous animations or transforms using "
            "@media (prefers-reduced-motion: reduce) or prefers-reduced-motion: no-preference."
        ),
        aliases=["reduced-motion", "a11y:motion", "prefers-reduced-motion"],
        non_compliant_example=".spin { animation: spin 1s linear infinite; }\n",
        compliant_example=(
            "@media (prefers-reduced-motion: no-preference) {\n"
            "  .spin { animation: spin 1s linear infinite; }\n"
            "}\n"
        ),
    ),
    # 13. S9423: Avoid !important in declarations
    "S9423": QualityRuleSpec(
        rule_id="S9423",
        rspec_id="RSPEC-9423",
        severity=QualitySeverity.HIGH,
        category=QualityCategory.MAINTAINABILITY,
        title='Remove this "!important" and resolve the specificity conflict instead',
        explanation=(
            "Using `!important` breaks the natural CSS cascade and specificity model. Future "
            "overrides become nearly impossible without adding more `!important` declarations, "
            "creating an unmaintainable specificity war."
        ),
        aliases=["no-important", "css:important"],
        non_compliant_example=".button { background: red !important; }\n",
        compliant_example=".container .button { background: red; }\n",
    ),
    # 14. S7924: Minimal contrast requirement
    "S7924": QualityRuleSpec(
        rule_id="S7924",
        rspec_id="RSPEC-7924",
        severity=QualitySeverity.HIGH,
        category=QualityCategory.ACCESSIBILITY,
        title="Text does not meet the minimal contrast requirement with its background",
        explanation=(
            "WCAG 2.1 AA requires a contrast ratio of at least 4.5:1 for normal text and 3:1 for "
            "large text against its background so users with low vision or color deficiencies "
            "can read it comfortably."
        ),
        aliases=["color-contrast", "a11y:contrast", "wcag:contrast"],
        non_compliant_example=".text { color: white; background: rgba(255, 255, 255, 0.05); }\n",
        compliant_example=".text { color: #f8fafc; background: #0f172a; }\n",
    ),
    # 15. S4323: TypeScript explicit 'any'
    "S4323": QualityRuleSpec(
        rule_id="S4323",
        rspec_id="RSPEC-4323",
        severity=QualitySeverity.MEDIUM,
        category=QualityCategory.MAINTAINABILITY,
        title="The 'any' type should not be used as an explicit type annotation or type assertion",
        explanation=(
            "Using `any` turns off TypeScript's type checker for that symbol, propagating "
            "untyped operations across callers and undermining static type safety. "
            "Use `unknown`, explicit interfaces, or generics instead."
        ),
        aliases=["no-explicit-any", "ts:no-any", "typescript:S4323"],
        non_compliant_example="const user: any = fetchUser();\n",
        compliant_example="const user: UserProfile = fetchUser();\n",
    ),
    # 16. S6544: TypeScript unhandled floating promise
    "S6544": QualityRuleSpec(
        rule_id="S6544",
        rspec_id="RSPEC-6544",
        severity=QualitySeverity.HIGH,
        category=QualityCategory.RELIABILITY,
        title="Promises should not be left floating without await, catch, or void operator",
        explanation=(
            "A floating promise executes asynchronously without error handling. If the promise "
            "rejects, it causes an unhandled promise rejection error that can crash node runtimes "
            "or lead to silent failure in browser workflows."
        ),
        aliases=["no-floating-promises", "floating-promise", "typescript:S6544"],
        non_compliant_example="syncData(); // Unhandled promise\n",
        compliant_example="await syncData(); // Or void syncData().catch(logError);\n",
    ),
    # 17. S6852: JSX img elements must have an alt attribute
    "S6852": QualityRuleSpec(
        rule_id="S6852",
        rspec_id="RSPEC-6852",
        severity=QualitySeverity.HIGH,
        category=QualityCategory.ACCESSIBILITY,
        title="All '<img>' elements must have an 'alt' attribute for screen reader accessibility",
        explanation=(
            "Screen readers announce images using their `alt` attribute. An image without an "
            "`alt` attribute forces screen readers to read the file URL or ignore the image, "
            "violating WCAG 1.1.1 (Non-text Content)."
        ),
        aliases=["jsx-no-alt", "jsx-a11y/alt-text", "a11y:img-alt"],
        non_compliant_example='<img src="/logo.svg" />\n',
        compliant_example='<img src="/logo.svg" alt="Company Logo" />\n',
    ),
    # 18. S6848: Non-interactive elements with onClick must have ARIA role
    "S6848": QualityRuleSpec(
        rule_id="S6848",
        rspec_id="RSPEC-6848",
        severity=QualitySeverity.HIGH,
        category=QualityCategory.ACCESSIBILITY,
        title=(
            "Non-interactive elements with click handlers must have an accessible role "
            "and keyboard listener"
        ),
        explanation=(
            "Attaching an `onClick` handler to a `<div>` or `<span>` without `role='button'` "
            "and keyboard event listeners (`onKeyDown`/`onKeyUp`) prevents keyboard and screen "
            "reader users from activating the action, violating WCAG 2.1.1 (Keyboard Accessible)."
        ),
        aliases=["click-events-have-key-events", "jsx-a11y/click-events-have-key-events"],
        non_compliant_example="<div onClick={toggleSidebar}>Toggle</div>\n",
        compliant_example='<button type="button" onClick={toggleSidebar}>Toggle</button>\n',
    ),
    # 19. S6505: Dockerfile use COPY instead of ADD
    "S6505": QualityRuleSpec(
        rule_id="S6505",
        rspec_id="RSPEC-6505",
        severity=QualitySeverity.LOW,
        category=QualityCategory.MAINTAINABILITY,
        title="Use 'COPY' instead of 'ADD' for local files and directories in Dockerfile",
        explanation=(
            "The `ADD` instruction has additional capabilities like extracting local tar archives "
            "and fetching remote URLs. For regular local file copying, `COPY` is more explicit, "
            "predictable, and safer."
        ),
        aliases=["dockerfile-use-copy", "docker:use-copy"],
        non_compliant_example="ADD src/ /app/src/\n",
        compliant_example="COPY src/ /app/src/\n",
    ),
    # 20. S6504: Dockerfile duplicate CMD or ENTRYPOINT
    "S6504": QualityRuleSpec(
        rule_id="S6504",
        rspec_id="RSPEC-6504",
        severity=QualitySeverity.HIGH,
        category=QualityCategory.RELIABILITY,
        title="Multiple 'CMD' or 'ENTRYPOINT' instructions should not be defined in the same stage",
        explanation=(
            "In a Dockerfile build stage, only the last `CMD` or `ENTRYPOINT` instruction takes "
            "effect. Defining multiple instances is deceptive and creates dead configuration."
        ),
        aliases=["dockerfile-multiple-cmd", "docker:duplicate-cmd"],
        non_compliant_example='CMD ["node", "server.js"]\nCMD ["npm", "start"]\n',
        compliant_example='CMD ["node", "server.js"]\n',
    ),
    # 21. S6506: Dockerfile WORKDIR should be absolute
    "S6506": QualityRuleSpec(
        rule_id="S6506",
        rspec_id="RSPEC-6506",
        severity=QualitySeverity.MEDIUM,
        category=QualityCategory.MAINTAINABILITY,
        title="'WORKDIR' should use an absolute path instead of a relative path",
        explanation=(
            "Using relative paths in `WORKDIR` (e.g. `WORKDIR app`) depends on the previous "
            "working directory state, making the build brittle and confusing across base "
            "image updates."
        ),
        aliases=["dockerfile-relative-workdir", "docker:workdir-absolute"],
        non_compliant_example="WORKDIR app\n",
        compliant_example="WORKDIR /app\n",
    ),
    # 22. S3358: Nested ternary operators
    "S3358": QualityRuleSpec(
        rule_id="S3358",
        rspec_id="RSPEC-3358",
        severity=QualitySeverity.MEDIUM,
        category=QualityCategory.MAINTAINABILITY,
        title="Ternary operators should not be nested",
        explanation=(
            "Nesting conditional expressions (ternary operators) reduces readability and makes "
            "code harder to understand and maintain. Extract nested conditionals into independent "
            "if-elif-else statements."
        ),
        aliases=["nested-ternary", "python:S3358", "nested-conditional-expression"],
        non_compliant_example="res = 'a' if cond1 else ('b' if cond2 else 'c')\n",
        compliant_example=(
            "if cond1:\n    res = 'a'\nelif cond2:\n    res = 'b'\nelse:\n    res = 'c'\n"
        ),
    ),
    # 23. S125: Commented-out code
    "S125": QualityRuleSpec(
        rule_id="S125",
        rspec_id="RSPEC-125",
        severity=QualitySeverity.LOW,
        category=QualityCategory.CODE_SMELL,
        title="Sections of code should not be commented out",
        explanation=(
            "Commented-out code litters the codebase and breeds confusion about why it was left "
            "behind. Rely on Git version control for history and delete obsolete code blocks."
        ),
        aliases=["ERA001", "commented-out-code"],
        non_compliant_example="# def old_logic():\n#     return False\n",
        compliant_example="# Explain algorithm intent rather than preserving disabled code\n",
    ),
    # 24. S1172: Unused function parameters
    "S1172": QualityRuleSpec(
        rule_id="S1172",
        rspec_id="RSPEC-1172",
        severity=QualitySeverity.MEDIUM,
        category=QualityCategory.CODE_SMELL,
        title="Unused function and method parameters should be removed",
        explanation=(
            "Parameters declared but never read in the function body clutter signatures and "
            "confuse callers. Remove unused parameters or prefix them with an underscore."
        ),
        aliases=["ARG001", "ARG002", "unused-parameter"],
        non_compliant_example="def calculate(total, tax, unused_arg):\n    return total + tax\n",
        compliant_example="def calculate(total, tax):\n    return total + tax\n",
    ),
    # 25. S5720: Built-in shadowing
    "S5720": QualityRuleSpec(
        rule_id="S5720",
        rspec_id="RSPEC-5720",
        severity=QualitySeverity.MEDIUM,
        category=QualityCategory.CODE_SMELL,
        title="Identifiers should not shadow built-in Python names",
        explanation=(
            "Reusing built-in names (such as id, list, type, input) as local variable or parameter "
            "names obscures the built-in functionality and leads to subtle runtime bugs."
        ),
        aliases=["A001", "A002", "A003", "shadowed-builtin"],
        non_compliant_example="def find_item(id, list):\n    pass\n",
        compliant_example="def find_item(item_id, item_list):\n    pass\n",
    ),
    # 26. S1192: Duplicated string literals
    "S1192": QualityRuleSpec(
        rule_id="S1192",
        rspec_id="RSPEC-1192",
        severity=QualitySeverity.LOW,
        category=QualityCategory.CODE_SMELL,
        title="String literals should not be duplicated",
        explanation=(
            "Duplicated string literals make refactoring error-prone. Defining a constant reduces "
            "the risk of typos and improves maintainability across call sites."
        ),
        aliases=["duplicated-literal", "literal-duplication"],
        non_compliant_example="status = 'PENDING'\nupdate('PENDING')\nlog('PENDING')\n",
        compliant_example="STATUS_PENDING = 'PENDING'\nstatus = STATUS_PENDING\n",
    ),
    # 27. S1144: Unused private function or method
    "S1144": QualityRuleSpec(
        rule_id="S1144",
        rspec_id="RSPEC-1144",
        severity=QualitySeverity.MEDIUM,
        category=QualityCategory.MAINTAINABILITY,
        title="Unused private functions and methods should be removed",
        explanation=(
            "Private functions or methods that are never called from within their module or class "
            "are dead code. Removing them improves signal-to-noise ratio and reduces audit debt."
        ),
        aliases=["unused-private-method", "dead-function"],
        non_compliant_example="def _unused_internal():\n    pass\n",
        compliant_example="# Remove unused internal helpers or wire into caller\n",
    ),
    # 28. S5754: Generic or blind exception handling
    "S5754": QualityRuleSpec(
        rule_id="S5754",
        rspec_id="RSPEC-5754",
        severity=QualitySeverity.HIGH,
        category=QualityCategory.RELIABILITY,
        title="Except clauses should not catch generic Exception or pass silently",
        explanation=(
            "Catching base Exception or passing silently swallows unexpected programming bugs, "
            "system interrupts, and memory errors, making troubleshooting difficult."
        ),
        aliases=["TRY002", "TRY302", "blind-except", "broad-except"],
        non_compliant_example="try:\n    do_work()\nexcept Exception:\n    pass\n",
        compliant_example=(
            "try:\n    do_work()\nexcept (IOError, ValueError) as err:\n    logger.error(err)\n"
        ),
    ),
    # 29. S5713: Nested "with" statements should be collapsed
    "S5713": QualityRuleSpec(
        rule_id="S5713",
        rspec_id="RSPEC-5713",
        severity=QualitySeverity.LOW,
        category=QualityCategory.CODE_SMELL,
        title='Nested "with" statements should be collapsed into a single statement',
        explanation=(
            "Multiple nested 'with' statements increase code indentation without adding value. "
            "Python supports combining multiple context managers in a single 'with' statement."
        ),
        aliases=["SIM117", "multiple-with-statements", "nested-with"],
        non_compliant_example="with open('a') as f1:\n    with open('b') as f2:\n        pass\n",
        compliant_example="with open('a') as f1, open('b') as f2:\n    pass\n",
    ),
    # 30. S1244: Exact floating-point comparisons
    "S1244": QualityRuleSpec(
        rule_id="S1244",
        rspec_id="RSPEC-1244",
        severity=QualitySeverity.MEDIUM,
        category=QualityCategory.RELIABILITY,
        title="Floating point numbers should not be tested for exact equality",
        explanation=(
            "Floating point math is inherently subject to rounding and precision errors. Direct "
            "equality comparisons (== or !=) can lead to unpredictable branching bugs. Use "
            "math.isclose() or define an explicit epsilon threshold instead."
        ),
        aliases=["PLR1736", "float-equality", "float-equal"],
        non_compliant_example="if result == 3.14159:\n    do_work()\n",
        compliant_example=(
            "import math\nif math.isclose(result, 3.14159, abs_tol=1e-5):\n    do_work()\n"
        ),
    ),
    # 31. S9409: Deprecated syntax should be modernized
    "S9409": QualityRuleSpec(
        rule_id="S9409",
        rspec_id="RSPEC-9409",
        severity=QualitySeverity.LOW,
        category=QualityCategory.MAINTAINABILITY,
        title="Deprecated syntax should be modernized to current Python standards",
        explanation=(
            "Using obsolete type annotations, legacy super() calls, or redundant formatting idioms "
            "causes friction and prevents taking advantage of Python 3.10+ language optimizations."
        ),
        aliases=["UP006", "UP035", "UP007", "pyupgrade", "modernize-syntax"],
        non_compliant_example="from typing import List, Dict\nx: List[str] = []\n",
        compliant_example="x: list[str] = []\n",
    ),
    # 32. S3516: Functions should not always return the same value
    "S3516": QualityRuleSpec(
        rule_id="S3516",
        rspec_id="RSPEC-3516",
        severity=QualitySeverity.LOW,
        category=QualityCategory.CODE_SMELL,
        title="Functions should not always return the same literal constant on all paths",
        explanation=(
            "A function or method that unconditionally returns the same constant on every code "
            "branch either has a redundant boolean return type or an incomplete implementation."
        ),
        aliases=["PLR0133", "identical-return", "same-return"],
        non_compliant_example=(
            "def is_valid(x):\n    if x > 0:\n        return True\n    return True\n"
        ),
        compliant_example="def is_valid(x):\n    return x > 0\n",
    ),
    # 33. S4144: Functions should not have identical implementations
    "S4144": QualityRuleSpec(
        rule_id="S4144",
        rspec_id="RSPEC-4144",
        category=QualityCategory.MAINTAINABILITY,
        severity=QualitySeverity.MEDIUM,
        title="Functions should not have identical implementations",
        explanation=(
            "Having two functions with the same implementation is suspicious; it is likely "
            "that should one be changed, the other should be changed too, or that one was a "
            "copy-paste error. If two functions really do the same thing, they should be "
            "merged or one should call the other."
        ),
        aliases=["identical-functions", "python:S4144", "duplicate-function-implementation"],
        non_compliant_example=(
            "def calculate_tax(amount):\n"
            "    return amount * 0.2\n\n"
            "def compute_vat(amount):\n"
            "    return amount * 0.2\n"
        ),
        compliant_example=(
            "def calculate_tax(amount):\n"
            "    return amount * 0.2\n\n"
            "def compute_vat(amount):\n"
            "    return calculate_tax(amount)\n"
        ),
    ),
    # 34. S5843: Regular expressions should not be too complex
    "S5843": QualityRuleSpec(
        rule_id="S5843",
        rspec_id="RSPEC-5843",
        category=QualityCategory.MAINTAINABILITY,
        severity=QualitySeverity.MEDIUM,
        title="Regular expressions should not be too complex",
        explanation=(
            "Complex regular expressions are difficult to understand and maintain, "
            "and can lead to poor performance and catastrophic backtracking (ReDoS)."
        ),
        aliases=["regex-complexity", "python:S5843", "regular-expression-complexity"],
        non_compliant_example=(
            're.compile(r"(?:a|b|c|d|e|f|g|h|i|j|k|l|m|n|o|p|q|r|s|t|u|v|w|x|y|z)+")\n'
        ),
        compliant_example=(
            "# Simplify regex by splitting into sequential substring checks or simpler patterns\n"
            "is_valid = line.startswith('#') and 'nosonar' in line\n"
        ),
    ),
}
