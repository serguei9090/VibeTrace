from vibetrace.indexer.parser import (
    GenericParser,
    GoParser,
    JavaScriptParser,
    RustParser,
    TypeScriptParser,
)
from vibetrace.indexer.resolver import ModuleResolver


class TestJavaScriptParser:
    """Test JavaScript/JSX parser"""

    def test_extract_functions_and_classes(self):
        parser = JavaScriptParser()
        code = b"""
class Calculator {
    add(a, b) {
        return a + b;
    }
}

function multiply(x, y) {
    return x * y;
}

const divide = (a, b) => a / b;
"""
        nodes, edges = parser.extract("test.js", code)

        # Should extract: Calculator class, add method, multiply function, divide arrow function
        assert len(nodes) >= 3
        assert any(n.name == "Calculator" and n.kind == "class" for n in nodes)
        assert any(n.name == "add" and n.kind == "function" for n in nodes)
        assert any(n.name == "multiply" and n.kind == "function" for n in nodes)

        # Should have edges (class defines method)
        assert len(edges) >= 1

    def test_handles_syntax_error(self):
        """Parser should not crash on malformed code"""
        parser = JavaScriptParser()
        bad_code = b"function broken( return 123"

        # Should return empty lists instead of crashing
        nodes, edges = parser.extract("bad.js", bad_code)
        assert isinstance(nodes, list)
        assert isinstance(edges, list)


class TestTypeScriptParser:
    """Test TypeScript/TSX parser"""

    def test_extract_interfaces_and_classes(self):
        parser = TypeScriptParser()
        code = b"""
interface User {
    name: string;
    age: number;
}

class UserService {
    getUser(id: number): User {
        return { name: "Test", age: 25 };
    }
}

function processUser(user: User): void {
    console.log(user.name);
}
"""
        nodes, _ = parser.extract("test.ts", code)

        # Should extract: User interface, UserService class, getUser method, processUser function
        assert len(nodes) >= 3
        assert any(n.name == "User" and n.kind == "interface" for n in nodes)
        assert any(n.name == "UserService" and n.kind == "class" for n in nodes)
        assert any(n.name == "processUser" and n.kind == "function" for n in nodes)

    def test_handles_syntax_error(self):
        """Parser should not crash on malformed TypeScript"""
        parser = TypeScriptParser()
        bad_code = b"interface Broken { name string }"  # Missing colon

        nodes, edges = parser.extract("bad.ts", bad_code)
        assert isinstance(nodes, list)
        assert isinstance(edges, list)

    def test_barrel_re_exports(self, tmp_path):
        """TypeScript parser handles re-exports from other modules in barrels"""
        from vibetrace.indexer.resolver import ModuleResolver

        (tmp_path / "components").mkdir()
        button_file = tmp_path / "components" / "Button.tsx"
        button_file.write_text("export function Button() { return null; }", encoding="utf-8")
        barrel_file = tmp_path / "components" / "index.ts"
        barrel_file.write_text("export { Button } from './Button';", encoding="utf-8")

        resolver = ModuleResolver(tmp_path)
        parser = TypeScriptParser(resolver=resolver)

        nodes, edges = parser.extract(
            "components/index.ts",
            b"export { Button } from './Button';",
        )
        assert any(n.name == "Button" for n in nodes)
        assert any(e.relation_type == "imports" for e in edges)
        assert any(e.relation_type == "references" for e in edges)

    def test_ts_enums_types_and_constants(self):
        """TypeScript parser extracts enums, type aliases, and exported constants."""
        parser = TypeScriptParser()
        code = b"""
export type UserId = string;
export enum Role { ADMIN, USER }
export const MAX_RETRIES = 5;
export let config = { timeout: 1000 };
"""
        nodes, _edges = parser.extract("types.ts", code)
        names = {n.name: n.kind for n in nodes}
        assert "UserId" in names
        assert names["UserId"] == "interface"
        assert "Role" in names
        assert names["Role"] == "enum"
        assert "MAX_RETRIES" in names
        assert names["MAX_RETRIES"] == "variable"
        assert "config" in names
        assert names["config"] == "variable"


class TestGoParser:
    """Test Go parser"""

    def test_extract_structs_and_functions(self):
        parser = GoParser()
        code = b"""
package main

type Person struct {
    Name string
    Age  int
}

func (p *Person) Greet() string {
    return "Hello, " + p.Name
}

func main() {
    println("Hello, World!")
}
"""
        nodes, _ = parser.extract("test.go", code)

        # Should extract: Greet method, main function
        assert len(nodes) >= 2
        assert any(n.name == "Greet" for n in nodes)
        assert any(n.name == "main" for n in nodes)

    def test_handles_syntax_error(self):
        """Parser should not crash on malformed Go code"""
        parser = GoParser()
        bad_code = b"func broken( return 123"

        nodes, edges = parser.extract("bad.go", bad_code)
        assert isinstance(nodes, list)
        assert isinstance(edges, list)


class TestRustParser:
    """Test Rust parser"""

    def test_extract_structs_and_traits(self):
        parser = RustParser()
        code = b"""
struct Point {
    x: i32,
    y: i32,
}

trait Drawable {
    fn draw(&self);
}

impl Point {
    fn new(x: i32, y: i32) -> Point {
        Point { x, y }
    }
}

fn distance(p1: &Point, p2: &Point) -> f64 {
    let dx = (p2.x - p1.x) as f64;
    let dy = (p2.y - p1.y) as f64;
    (dx * dx + dy * dy).sqrt()
}
"""
        nodes, _ = parser.extract("test.rs", code)

        # Should extract: Point struct, Drawable trait, new function, distance function
        assert len(nodes) >= 3
        assert any(n.name == "Point" and n.kind == "struct" for n in nodes)
        assert any(n.name == "distance" for n in nodes)

    def test_handles_syntax_error(self):
        """Parser should not crash on malformed Rust code"""
        parser = RustParser()
        bad_code = b"fn broken( -> i32 { 123 }"

        nodes, edges = parser.extract("bad.rs", bad_code)
        assert isinstance(nodes, list)
        assert isinstance(edges, list)

    def test_rust_cross_file_impl_resolution(self):
        """RustParser resolves imported struct for impl blocks."""
        parser = RustParser()
        code = b"""
impl User {
    fn get_id(&self) -> u32 {
        1
    }
}
"""
        nodes, edges = parser.extract("methods.rs", code)
        fn_node = next((n for n in nodes if n.name == "get_id"), None)
        assert fn_node is not None
        assert fn_node.qualified_name == "User.get_id"
        # Should link struct to method with defines edge
        assert any(e.relation_type == "defines" and e.to_node_id == fn_node.id for e in edges)


class TestGenericParser:
    """Test generic parser for C-family languages"""

    def test_java_extraction(self):
        parser = GenericParser("java")
        code = b"""
public class HelloWorld {
    public static void main(String[] args) {
        System.out.println("Hello, World!");
    }
    
    private void helper() {
        // Helper method
    }
}
"""
        nodes, _ = parser.extract("test.java", code)

        # Should extract at least the class and methods
        assert len(nodes) > 0
        assert any(n.kind in ["class", "function"] for n in nodes)

    def test_cpp_extraction(self):
        parser = GenericParser("cpp")
        code = b"""
class Vector3 {
public:
    float x, y, z;
    
    Vector3(float x, float y, float z) : x(x), y(y), z(z) {}
    
    float magnitude() {
        return sqrt(x*x + y*y + z*z);
    }
};

int main() {
    return 0;
}
"""
        nodes, _ = parser.extract("test.cpp", code)

        # Should extract class and functions
        assert len(nodes) > 0
        assert any(n.kind in ["class", "function"] for n in nodes)

    def test_handles_syntax_error(self):
        """Parser should not crash on malformed code"""
        parser = GenericParser("java")
        bad_code = b"public class Broken { void method( }"

        nodes, edges = parser.extract("bad.java", bad_code)
        assert isinstance(nodes, list)
        assert isinstance(edges, list)

    def test_generic_parser_call_extraction(self):
        """GenericParser must extract calls edges between functions."""
        parser = GenericParser("cpp")
        code = b"""
void helper() {
}

int main() {
    helper();
    return 0;
}
"""
        nodes, edges = parser.extract("main.cpp", code)
        call_edges = [e for e in edges if e.relation_type == "calls"]
        assert len(call_edges) >= 1
        main_node = next(n for n in nodes if n.name == "main")
        helper_node = next(n for n in nodes if n.name == "helper")
        assert any(
            e.from_node_id == main_node.id and e.to_node_id == helper_node.id for e in call_edges
        )


class TestEdgeCases:
    """Test edge cases across all parsers"""

    def test_module_resolver_supported_extensions(self, tmp_path):
        """ModuleResolver must track C, C++, Java, and systems files."""
        (tmp_path / "Main.java").write_text("public class Main {}", encoding="utf-8")
        (tmp_path / "native.cpp").write_text("int main() { return 0; }", encoding="utf-8")
        resolver = ModuleResolver(tmp_path)
        assert "Main.java" in resolver.known_files
        assert "native.cpp" in resolver.known_files

    def test_empty_file(self):
        """Empty files should return empty lists"""
        parser = JavaScriptParser()
        nodes, edges = parser.extract("empty.js", b"")
        assert nodes == []
        assert edges == []

    def test_whitespace_only(self):
        """Files with only whitespace should return empty lists"""
        parser = TypeScriptParser()
        nodes, edges = parser.extract("whitespace.ts", b"   \n\n  \t  \n")
        assert nodes == []
        assert edges == []

    def test_comments_only(self):
        """Files with only comments might not extract nodes"""
        parser = JavaScriptParser()
        code = b"""
// This is a comment
/* This is a multi-line
   comment */
"""
        nodes, edges = parser.extract("comments.js", code)
        # Should not crash, may return empty
        assert isinstance(nodes, list)
        assert isinstance(edges, list)
