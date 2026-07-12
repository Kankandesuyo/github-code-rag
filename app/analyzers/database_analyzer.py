import re
from pathlib import Path

from app.schemas.report_schema import DatabaseFinding


class DatabaseAnalyzer:
    def __init__(self, files: list[Path]) -> None:
        self.files = files

    def read_text_file(self, path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def analyze_python_database_file(self, content: str, relative: str) -> list[DatabaseFinding]:
        findings: list[DatabaseFinding] = []
        lines = content.splitlines()
        class_pattern = re.compile(r"^\s*class\s+(?P<name>\w+)\((?P<bases>[^)]*)\):")
        column_pattern = re.compile(r"^\s*(?P<name>\w+)\s*=\s*(?P<expr>.+)")
        current_model = ""
        current_technology = ""
        model_fields: dict[str, list[str]] = {}
        model_relationships: dict[str, list[str]] = {}

        for line_number, line in enumerate(lines, start=1):
            class_match = class_pattern.match(line)
            if class_match:
                bases = class_match.group("bases")
                current_model = class_match.group("name")
                current_technology = ""
                if "models.Model" in bases:
                    current_technology = "Django ORM"
                elif re.search(r"\b(Base|DeclarativeBase|db\.Model)\b", bases):
                    current_technology = "SQLAlchemy"
                if current_technology:
                    model_fields.setdefault(current_model, [])
                    model_relationships.setdefault(current_model, [])
                    findings.append(
                        DatabaseFinding(
                            technology=current_technology,
                            file_path=relative,
                            line=line_number,
                            detail=f"model {current_model}",
                            model_name=current_model,
                        )
                    )

            column_match = column_pattern.match(line)
            if column_match:
                expr = column_match.group("expr")
                field_name = column_match.group("name")
                if re.search(r"\b(Column|mapped_column|relationship|ForeignKey)\b", expr):
                    relationship = re.search(r"\b(relationship|ForeignKey)\b", expr) is not None
                    if current_model:
                        target = model_relationships if relationship else model_fields
                        target.setdefault(current_model, []).append(field_name)
                    findings.append(
                        DatabaseFinding(
                            technology="SQLAlchemy",
                            file_path=relative,
                            line=line_number,
                            detail=f"{field_name}: {line.strip()[:140]}",
                            model_name=current_model,
                            fields=[field_name] if not relationship else [],
                            relationships=[field_name] if relationship else [],
                        )
                    )
                elif re.search(r"\bmodels\.(CharField|TextField|IntegerField|BigIntegerField|BooleanField|DateTimeField|DateField|ForeignKey|OneToOneField|ManyToManyField|JSONField|DecimalField)\b", expr):
                    relationship = re.search(r"\bmodels\.(ForeignKey|OneToOneField|ManyToManyField)\b", expr) is not None
                    if current_model:
                        target = model_relationships if relationship else model_fields
                        target.setdefault(current_model, []).append(field_name)
                    findings.append(
                        DatabaseFinding(
                            technology="Django ORM",
                            file_path=relative,
                            line=line_number,
                            detail=f"{field_name}: {line.strip()[:140]}",
                            model_name=current_model,
                            fields=[field_name] if not relationship else [],
                            relationships=[field_name] if relationship else [],
                        )
                    )
            elif re.search(r"\b(declarative_base|sqlalchemy|Mapped\[)\b", line):
                findings.append(
                    DatabaseFinding(
                        technology="SQLAlchemy",
                        file_path=relative,
                        line=line_number,
                        detail=line.strip()[:160],
                    )
                )
        return findings

    def analyze_prisma_file(self, content: str, relative: str) -> list[DatabaseFinding]:
        findings: list[DatabaseFinding] = []
        current_model = ""
        for line_number, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            model_match = re.match(r"model\s+(\w+)\s+\{", stripped)
            datasource_match = re.match(r"datasource\s+(\w+)\s+\{", stripped)
            provider_match = re.match(r"provider\s*=\s*[\"']([^\"']+)[\"']", stripped)
            field_match = re.match(r"(\w+)\s+([A-Za-z][\w\[\]?]*)", stripped)
            if model_match:
                current_model = model_match.group(1)
                findings.append(
                    DatabaseFinding(
                        technology="Prisma",
                        file_path=relative,
                        line=line_number,
                        detail=f"model {current_model}",
                        model_name=current_model,
                    )
                )
            elif datasource_match:
                findings.append(
                    DatabaseFinding(
                        technology="Prisma",
                        file_path=relative,
                        line=line_number,
                        detail=f"datasource {datasource_match.group(1)}",
                    )
                )
            elif provider_match:
                findings.append(
                    DatabaseFinding(
                        technology="Prisma",
                        file_path=relative,
                        line=line_number,
                        detail=f"provider {provider_match.group(1)}",
                    )
                )
            elif current_model and field_match and not stripped.startswith("//"):
                field_type = field_match.group(2)
                is_relation = field_type[:1].isupper() or "@" in stripped and "relation" in stripped
                findings.append(
                    DatabaseFinding(
                        technology="Prisma",
                        file_path=relative,
                        line=line_number,
                        detail=f"{current_model}.{field_match.group(1)}: {field_type}",
                        model_name=current_model,
                        fields=[field_match.group(1)] if not is_relation else [],
                        relationships=[field_match.group(1)] if is_relation else [],
                    )
                )
            if stripped == "}":
                current_model = ""
        return findings

    def analyze_mongoose_file(self, content: str, relative: str) -> list[DatabaseFinding]:
        findings: list[DatabaseFinding] = []
        schema_pattern = re.compile(r"\b(?:new\s+)?mongoose\.Schema\(\s*\{", re.IGNORECASE)
        model_pattern = re.compile(r"\bmongoose\.model\(\s*[\"'`](?P<name>[^\"'`]+)[\"'`]", re.IGNORECASE)
        for match in schema_pattern.finditer(content):
            line = content[: match.start()].count("\n") + 1
            findings.append(
                DatabaseFinding(
                    technology="Mongoose",
                    file_path=relative,
                    line=line,
                    detail="schema definition",
                )
            )
        for match in model_pattern.finditer(content):
            line = content[: match.start()].count("\n") + 1
            findings.append(
                DatabaseFinding(
                    technology="Mongoose",
                    file_path=relative,
                    line=line,
                    detail=f"model {match.group('name')}",
                    model_name=match.group("name"),
                )
            )
        return findings

    def analyze(self, repo_path: Path) -> list[DatabaseFinding]:
        findings: list[DatabaseFinding] = []
        for path in self.files:
            suffix = path.suffix.lower()
            if suffix not in {".py", ".prisma", ".ts", ".js", ".tsx", ".jsx"} and path.name != "schema.prisma":
                continue
            content = self.read_text_file(path)
            relative = path.relative_to(repo_path).as_posix()
            if suffix == ".py":
                findings.extend(self.analyze_python_database_file(content, relative))
            if suffix == ".prisma" or path.name == "schema.prisma":
                findings.extend(self.analyze_prisma_file(content, relative))
            if suffix in {".js", ".ts", ".tsx", ".jsx"}:
                findings.extend(self.analyze_mongoose_file(content, relative))
        return findings[:160]
