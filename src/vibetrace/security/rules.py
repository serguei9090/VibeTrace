"""Built-in AST Security Rules Catalog with SonarQube-style guidance."""

from __future__ import annotations

from dataclasses import dataclass, field

from vibetrace.security.models import Severity


@dataclass(frozen=True)
class SecurityRuleSpec:
    """Specification of an AST security rule with explanatory rationale and compliant examples."""

    rule_id: str
    cwe_id: str
    severity: Severity
    title: str
    explanation: str
    non_compliant_example: str
    compliant_example: str
    compliant_by_lang: dict[str, str] = field(default_factory=dict)

    def get_compliant_example(self, lang: str) -> str:
        return self.compliant_by_lang.get(lang, self.compliant_example)


RULES_CATALOG: dict[str, SecurityRuleSpec] = {
    # 1. CWE-78: Command Injection
    "CWE-78": SecurityRuleSpec(
        rule_id="CWE-78",
        cwe_id="CWE-78",
        severity=Severity.CRITICAL,
        title=(
            "Improper Neutralization of Special Elements used in an OS Command "
            "('OS Command Injection')"
        ),
        explanation=(
            "Passing unsanitized strings to shell execution functions (`shell=True`, "
            "`os.system`, `exec()`) allows attackers to append arbitrary shell operators "
            "(`;`, `|`, `&&`) and execute unauthorized commands with process privileges."
        ),
        non_compliant_example="subprocess.run(f'ping {host}', shell=True)",
        compliant_example="subprocess.run(['ping', host], shell=False, check=True)",
        compliant_by_lang={
            "javascript": "cp.execFileSync('ping', [host], { stdio: 'ignore' });",
            "typescript": "cp.execFileSync('ping', [host], { stdio: 'ignore' });",
            "tsx": "cp.execFileSync('ping', [host], { stdio: 'ignore' });",
            "go": 'exec.CommandContext(ctx, "ping", host).Run()',
            "rust": 'Command::new("ping").arg(host).status()?',
        },
    ),
    # 2. CWE-95: Eval / Code Injection
    "CWE-95": SecurityRuleSpec(
        rule_id="CWE-95",
        cwe_id="CWE-95",
        severity=Severity.CRITICAL,
        title=(
            "Improper Neutralization of Directives in Dynamically Evaluated Code ('Eval Injection')"
        ),
        explanation=(
            "Dynamic evaluation functions (`eval`, `exec`, `new Function`) parse and execute raw "
            "strings as full programming statements within the runtime. Attackers can escape "
            "intended logic to access runtime objects, secrets, or spawn shell processes."
        ),
        non_compliant_example="result = eval(user_expression)",
        compliant_example="result = ast.literal_eval(user_expression)  # or safe math parser",
    ),
    # 3. CWE-798: Hardcoded Credentials / Secrets
    "CWE-798": SecurityRuleSpec(
        rule_id="CWE-798",
        cwe_id="CWE-798",
        severity=Severity.HIGH,
        title="Use of Hard-coded Credentials",
        explanation=(
            "Hardcoding credentials, passwords, or tokens directly into source code compromises "
            "secrets as soon as code is shared or version-controlled. Secrets must be read "
            "dynamically from environment variables or dedicated secret management vaults."
        ),
        non_compliant_example="AWS_SECRET_KEY = 'AKIAIOSFODNN7EXAMPLE'",
        compliant_example="AWS_SECRET_KEY = os.environ.get('AWS_SECRET_KEY')",
    ),
    # 4. CWE-327: Broken or Risky Cryptographic Algorithm
    "CWE-327": SecurityRuleSpec(
        rule_id="CWE-327",
        cwe_id="CWE-327",
        severity=Severity.MEDIUM,
        title="Use of a Broken or Risky Cryptographic Algorithm",
        explanation=(
            "Algorithms such as MD5 and SHA-1 suffer from proven cryptographic collisions. "
            "Never use them for password hashing, digital signatures, or tampering checks. "
            "Use SHA-256/SHA-512 for integrity, or Argon2id/bcrypt for password hashing."
        ),
        non_compliant_example="hashlib.md5(password.encode()).hexdigest()",
        compliant_example="hashlib.sha256(data.encode()).hexdigest()",
    ),
    # 5. CWE-502: Deserialization of Untrusted Data
    "CWE-502": SecurityRuleSpec(
        rule_id="CWE-502",
        cwe_id="CWE-502",
        severity=Severity.HIGH,
        title="Deserialization of Untrusted Data",
        explanation=(
            "Deserializing untrusted data with formats like Python `pickle` or unconstrained "
            "`yaml.load()` permits arbitrary object construction and remote code execution. "
            "Always use safe parsers like `json` or `yaml.safe_load()`."
        ),
        non_compliant_example="data = pickle.loads(untrusted_bytes)",
        compliant_example="data = json.loads(untrusted_bytes)  # or yaml.safe_load()",
    ),
    # 6. CWE-89: SQL Injection
    "CWE-89": SecurityRuleSpec(
        rule_id="CWE-89",
        cwe_id="CWE-89",
        severity=Severity.CRITICAL,
        title=(
            "Improper Neutralization of Special Elements used in an SQL Command ('SQL Injection')"
        ),
        explanation=(
            "Constructing SQL queries with f-strings, string concatenation (`+`), or `.format()` "
            "allows attackers to inject SQL syntax and read or modify arbitrary database tables. "
            "Always pass dynamic values via parameterized query placeholders (`?` or `%s`)."
        ),
        non_compliant_example="cursor.execute(f'SELECT * FROM users WHERE name = \"{user}\"')",
        compliant_example="cursor.execute('SELECT * FROM users WHERE name = ?', (user,))",
    ),
    # 7. CWE-22: Path Traversal
    "CWE-22": SecurityRuleSpec(
        rule_id="CWE-22",
        cwe_id="CWE-22",
        severity=Severity.HIGH,
        title="Improper Limitation of a Pathname to a Restricted Directory ('Path Traversal')",
        explanation=(
            "Joining or opening file paths constructed from untrusted input without verifying "
            "that the resolved canonical path stays inside the intended base directory allows "
            "`../` sequences to read or overwrite sensitive system files."
        ),
        non_compliant_example="open(f'/var/data/{user_filename}', 'rb').read()",
        compliant_example=(
            "target = (BASE_DIR / user_filename).resolve()\n"
            "if not target.is_relative_to(BASE_DIR.resolve()): raise ValueError('Invalid path')"
        ),
    ),
    # 8. CWE-79: Cross-Site Scripting (XSS)
    "CWE-79": SecurityRuleSpec(
        rule_id="CWE-79",
        cwe_id="CWE-79",
        severity=Severity.HIGH,
        title="Improper Neutralization of Input During Web Page Generation ('XSS')",
        explanation=(
            "Assigning unsanitized HTML via `innerHTML`, `dangerouslySetInnerHTML`, or `mark_safe` "
            "allows attackers to inject executable `<script>` payloads into the user's browser."
        ),
        non_compliant_example="el.innerHTML = userComment;",
        compliant_example="el.textContent = userComment;  // or DOMPurify.sanitize(userComment)",
    ),
    # 9. CWE-918: Server-Side Request Forgery (SSRF)
    "CWE-918": SecurityRuleSpec(
        rule_id="CWE-918",
        cwe_id="CWE-918",
        severity=Severity.HIGH,
        title="Server-Side Request Forgery (SSRF)",
        explanation=(
            "Fetching URLs constructed directly from untrusted input allows attackers to probe "
            "internal metadata services (e.g. `169.254.169.254`) or private intranet endpoints."
        ),
        non_compliant_example="requests.get(f'http://{user_host}/status')",
        compliant_example="if user_host not in ALLOWED_HOSTS: raise ValueError()\nrequests.get(f'https://{user_host}/status')",
    ),
    # 10. CWE-295: Improper Certificate Validation
    "CWE-295": SecurityRuleSpec(
        rule_id="CWE-295",
        cwe_id="CWE-295",
        severity=Severity.MEDIUM,
        title="Improper Certificate Validation",
        explanation=(
            "Disabling TLS certificate verification (`verify=False`, `rejectUnauthorized: false`) "
            "exposes connections to Man-in-the-Middle (MitM) interception and spoofing."
        ),
        non_compliant_example="requests.get('https://api.example.com', verify=False)",
        compliant_example="requests.get('https://api.example.com', verify=True)",
    ),
    # 11. CWE-347: Improper Verification of Cryptographic Signature (JWT)
    "CWE-347": SecurityRuleSpec(
        rule_id="CWE-347",
        cwe_id="CWE-347",
        severity=Severity.HIGH,
        title="Improper Verification of Cryptographic Signature ('Unverified JWT')",
        explanation=(
            "Decoding JWT tokens with `verify_signature: False` or `verify=False` allows "
            "attackers to forge arbitrary identity claims and escalate privileges."
        ),
        non_compliant_example="jwt.decode(token, options={'verify_signature': False})",
        compliant_example="jwt.decode(token, key=PUBLIC_KEY, algorithms=['RS256'])",
    ),
    # 12. CWE-611: Improper Restriction of XML External Entity Reference (XXE)
    "CWE-611": SecurityRuleSpec(
        rule_id="CWE-611",
        cwe_id="CWE-611",
        severity=Severity.HIGH,
        title="Improper Restriction of XML External Entity Reference ('XXE')",
        explanation=(
            "Parsing XML with external entity resolution enabled (`resolve_entities=True`) allows "
            "attackers to read local files (`file:///etc/passwd`) or trigger SSRF."
        ),
        non_compliant_example="lxml.etree.XMLParser(resolve_entities=True)",
        compliant_example="lxml.etree.XMLParser(resolve_entities=False, no_network=True)",
    ),
}
