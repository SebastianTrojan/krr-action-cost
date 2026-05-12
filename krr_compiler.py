from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings



IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


class ParseError(ValueError):
    pass


class QueryStatus(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNDEFINED = "UNDEFINED"


@dataclass(frozen=True)
class Literal:
    name: str
    positive: bool = True

    def holds_in(self, state: tuple[bool, ...], index_by_fluent: dict[str, int]) -> bool:
        return state[index_by_fluent[self.name]] is self.positive

    def __str__(self) -> str:
        return self.name if self.positive else f"!{self.name}"


@dataclass(frozen=True)
class ValueStatement:
    literal: Literal
    program: tuple[str, ...]
    source_line: int


@dataclass(frozen=True)
class EffectStatement:
    action: str
    literal: Literal
    preconditions: tuple[Literal, ...]
    source_line: int


@dataclass(frozen=True)
class CostStatement:
    action: str
    cost: int
    source_line: int


@dataclass(frozen=True)
class GoalQuery:
    goal: tuple[Literal, ...]
    program: tuple[str, ...]
    source_line: int

    def render(self) -> str:
        goal_text = ", ".join(str(literal) for literal in self.goal)
        program_text = ", ".join(self.program)
        return f"{goal_text} after {program_text}" if program_text else goal_text


@dataclass(frozen=True)
class MaxCostQuery:
    program: tuple[str, ...]
    cost: int
    source_line: int

    def render(self) -> str:
        return f"{', '.join(self.program)} executable with cost {self.cost}"


@dataclass(frozen=True)
class ExactCostQuery:
    program: tuple[str, ...]
    cost: int
    source_line: int

    def render(self) -> str:
        return f"{', '.join(self.program)} executable with exact cost {self.cost}"


Query = GoalQuery | MaxCostQuery | ExactCostQuery
DomainStatement = ValueStatement | EffectStatement | CostStatement


@dataclass(frozen=True)
class SourceLine:
    number: int
    text: str


@dataclass(frozen=True)
class Domain:
    value_statements: tuple[ValueStatement, ...]
    effect_statements: tuple[EffectStatement, ...]
    cost_statements: tuple[CostStatement, ...]


@dataclass
class EvaluationContext:
    fluents: tuple[str, ...]
    actions: tuple[str, ...]
    states: tuple[tuple[bool, ...], ...]
    transition_table: dict[tuple[str, tuple[bool, ...]], tuple[bool, ...]]
    cost_table: dict[tuple[str, tuple[bool, ...]], int]
    models: tuple[tuple[bool, ...], ...]
    index_by_fluent: dict[str, int]


@dataclass(frozen=True)
class LoadedInputs:
    domain: Domain
    queries: tuple[Query, ...]
    interactive_session: bool


def strip_comment(line: str) -> str:
    return line.split("#", 1)[0].strip()


def normalize_line(line: str) -> str:
    stripped = strip_comment(line)
    if stripped.endswith("."):
        stripped = stripped[:-1].strip()
    return stripped


def collect_source_lines(raw_lines: list[str]) -> list[SourceLine]:
    lines: list[SourceLine] = []
    for number, raw_line in enumerate(raw_lines, start=1):
        normalized = normalize_line(raw_line)
        if normalized:
            lines.append(SourceLine(number, normalized))
    return lines


def read_lines(path: Path) -> list[SourceLine]:
    text = path.read_text(encoding="utf-8")
    return collect_source_lines(text.splitlines())


def unwrap_enclosure(text: str, left: str, right: str) -> str:
    stripped = text.strip()
    if stripped.startswith(left) and stripped.endswith(right):
        return stripped[1:-1].strip()
    return stripped


def parse_identifier(text: str, line_number: int, kind: str) -> str:
    token = text.strip()
    if not IDENTIFIER_RE.fullmatch(token):
        raise ParseError(f"Line {line_number}: invalid {kind} '{text}'.")
    return token



def parse_literal_list(text: str, line_number: int) -> tuple[Literal, ...]:
    cleaned = unwrap_enclosure(text, "{", "}")
    cleaned = unwrap_enclosure(cleaned, "(", ")")
    if not cleaned:
        return ()
    parts = [part.strip() for part in cleaned.split(",") if part.strip()]
    if not parts:
        return ()
    return tuple(parse_literal_strict(part, line_number) for part in parts)



def parse_literal_strict(text: str, line_number: int) -> Literal:
    token = text.strip()
    positive = True
    if token.startswith("!"):
        positive = False
        token = token[1:].strip()
    name = parse_identifier(token, line_number, "fluent")
    return Literal(name=name, positive=positive)


def parse_literal(text: str, line_number: int) -> Literal:
    token = text.strip()
    positive = True
    if token.startswith("!"):
        positive = False
        token = token[1:].strip()
    name = parse_identifier(token, line_number, "fluent")
    return Literal(name=name, positive=positive)


def parse_program(text: str, line_number: int) -> tuple[str, ...]:
    cleaned = unwrap_enclosure(text, "(", ")")
    if not cleaned:
        return ()
    parts = [part.strip() for part in cleaned.split(",") if part.strip()]
    return tuple(parse_identifier(part, line_number, "action") for part in parts)


def parse_domain_entries(source_line: SourceLine) -> tuple[DomainStatement, ...]:
    line = source_line.text

    if line.lower().startswith("initially "):
        literals = parse_literal_list(line[10:].strip(), source_line.number)
        if not literals:
            raise ParseError(f"Line {source_line.number}: initially requires at least one literal.")
        return tuple(
            ValueStatement(literal=literal, program=(), source_line=source_line.number)
            for literal in literals
        )

    if " causes " in line:
        action_text, remainder = line.split(" causes ", 1)
        action = parse_identifier(action_text, source_line.number, "action")
        if " if " in remainder:
            literal_text, preconditions_text = remainder.split(" if ", 1)
            preconditions = parse_literal_list(preconditions_text, source_line.number)
        else:
            literal_text = remainder
            preconditions = ()
        literals = parse_literal_list(literal_text, source_line.number)
        if not literals:
            raise ParseError(f"Line {source_line.number}: causes requires at least one literal.")
        return tuple(
            EffectStatement(
                action=action,
                literal=literal,
                preconditions=preconditions,
                source_line=source_line.number,
            )
            for literal in literals
        )

    if " costs " in line:
        action_text, cost_text = line.split(" costs ", 1)
        action = parse_identifier(action_text, source_line.number, "action")
        try:
            cost = int(cost_text.strip())
        except ValueError as exc:
            raise ParseError(f"Line {source_line.number}: invalid cost '{cost_text}'.") from exc
        if cost <= 0:
            raise ParseError(f"Line {source_line.number}: cost must be greater than zero.")
        return (CostStatement(action=action, cost=cost, source_line=source_line.number),)

    if " after " in line:
        literal_text, program_text = line.split(" after ", 1)
        literals = parse_literal_list(literal_text, source_line.number)
        if not literals:
            raise ParseError(f"Line {source_line.number}: after requires at least one literal.")
        program = parse_program(program_text, source_line.number)
        return tuple(
            ValueStatement(literal=literal, program=program, source_line=source_line.number)
            for literal in literals
        )

    raise ParseError(f"Line {source_line.number}: could not parse domain statement '{line}'.")


def parse_query_line(source_line: SourceLine) -> Query:
    line = source_line.text

    if " executable with exact cost " in line:
        program_text, cost_text = line.split(" executable with exact cost ", 1)
        program = parse_program(program_text, source_line.number)
        try:
            cost = int(cost_text.strip())
        except ValueError as exc:
            raise ParseError(f"Line {source_line.number}: invalid cost '{cost_text}'.") from exc
        if cost < 0:
            raise ParseError(f"Line {source_line.number}: query cost must be non-negative.")
        return ExactCostQuery(program=program, cost=cost, source_line=source_line.number)

    if " executable with cost " in line:
        program_text, cost_text = line.split(" executable with cost ", 1)
        program = parse_program(program_text, source_line.number)
        try:
            cost = int(cost_text.strip())
        except ValueError as exc:
            raise ParseError(f"Line {source_line.number}: invalid cost '{cost_text}'.") from exc
        if cost < 0:
            raise ParseError(f"Line {source_line.number}: query cost must be non-negative.")
        return MaxCostQuery(program=program, cost=cost, source_line=source_line.number)

    if " after " in line:
        goal_text, program_text = line.split(" after ", 1)
        goal = parse_literal_list(goal_text, source_line.number)
        if not goal:
            raise ParseError(f"Line {source_line.number}: goal query requires at least one literal.")
        program = parse_program(program_text, source_line.number)
        return GoalQuery(goal=goal, program=program, source_line=source_line.number)

    raise ParseError(f"Line {source_line.number}: could not parse query '{line}'.")


def parse_domain_lines(lines: list[SourceLine]) -> Domain:
    values: list[ValueStatement] = []
    effects: list[EffectStatement] = []
    costs: list[CostStatement] = []

    for source_line in lines:
        for statement in parse_domain_entries(source_line):
            if isinstance(statement, ValueStatement):
                values.append(statement)
            elif isinstance(statement, EffectStatement):
                effects.append(statement)
            else:
                costs.append(statement)

    return Domain(
        value_statements=tuple(values),
        effect_statements=tuple(effects),
        cost_statements=tuple(costs),
    )


def parse_query_lines(lines: list[SourceLine]) -> tuple[Query, ...]:
    return tuple(parse_query_line(line) for line in lines)


def split_spec_lines(
    lines: list[SourceLine],
    source_name: str,
    require_explicit_sections: bool = False,
) -> tuple[list[SourceLine], list[SourceLine]]:
    domain_lines: list[SourceLine] = []
    query_lines: list[SourceLine] = []
    current_section = "domain" if not require_explicit_sections else ""
    saw_domain_header = False
    saw_queries_header = False

    for source_line in lines:
        lowered = source_line.text.lower()
        if lowered == "[domain]":
            if saw_domain_header:
                raise ParseError(f"{source_name}: duplicate [domain] section.")
            if saw_queries_header:
                raise ParseError(f"{source_name}: [domain] must appear before [queries].")
            current_section = "domain"
            saw_domain_header = True
            continue
        if lowered == "[queries]":
            if saw_queries_header:
                raise ParseError(f"{source_name}: duplicate [queries] section.")
            if require_explicit_sections and not saw_domain_header:
                raise ParseError(f"{source_name}: missing [domain] section before [queries].")
            current_section = "queries"
            saw_queries_header = True
            continue

        if require_explicit_sections and not current_section:
            raise ParseError(f"{source_name}: missing [domain] section before the first statement.")

        if current_section == "domain":
            domain_lines.append(source_line)
        else:
            query_lines.append(source_line)

    if require_explicit_sections:
        if not saw_domain_header:
            raise ParseError(f"{source_name}: missing [domain] section.")
        if not saw_queries_header:
            raise ParseError(f"{source_name}: missing [queries] section.")
        if not domain_lines:
            raise ParseError(f"{source_name}: no domain statements found.")
        if not query_lines:
            raise ParseError(f"{source_name}: no query statements found.")
    elif not domain_lines and (saw_domain_header or saw_queries_header):
        raise ParseError(f"{source_name}: no domain statements found.")

    return domain_lines, query_lines


def parse_spec_lines(
    lines: list[SourceLine],
    source_name: str,
    require_explicit_sections: bool = False,
) -> tuple[Domain, tuple[Query, ...]]:
    domain_lines, query_lines = split_spec_lines(
        lines,
        source_name,
        require_explicit_sections=require_explicit_sections,
    )

    domain = parse_domain_lines(domain_lines)
    queries = parse_query_lines(query_lines)
    return domain, queries


def parse_spec_file(path: Path) -> tuple[Domain, tuple[Query, ...]]:
    return parse_spec_lines(read_lines(path), str(path), require_explicit_sections=True)


def parse_spec_text(text: str, source_name: str = "<stdin>") -> tuple[Domain, tuple[Query, ...]]:
    return parse_spec_lines(
        collect_source_lines(text.splitlines()),
        source_name,
        require_explicit_sections=True,
    )


def gather_signature(domain: Domain) -> tuple[tuple[str, ...], tuple[str, ...]]:
    fluents: set[str] = set()
    actions: set[str] = set()

    for statement in domain.value_statements:
        fluents.add(statement.literal.name)
        actions.update(statement.program)

    for statement in domain.effect_statements:
        actions.add(statement.action)
        fluents.add(statement.literal.name)
        for precondition in statement.preconditions:
            fluents.add(precondition.name)

    for statement in domain.cost_statements:
        actions.add(statement.action)

    return tuple(sorted(fluents)), tuple(sorted(actions))


def validate_queries(queries: tuple[Query, ...], fluents: tuple[str, ...], actions: tuple[str, ...]) -> None:
    known_fluents = set(fluents)
    known_actions = set(actions)

    for query in queries:
        if isinstance(query, GoalQuery):
            for literal in query.goal:
                if literal.name not in known_fluents:
                    raise ParseError(
                        f"Line {query.source_line}: query references unknown fluent '{literal.name}'."
                    )
            for action in query.program:
                if action not in known_actions:
                    raise ParseError(
                        f"Line {query.source_line}: query references unknown action '{action}'."
                    )
        else:
            for action in query.program:
                if action not in known_actions:
                    raise ParseError(
                        f"Line {query.source_line}: query references unknown action '{action}'."
                    )


def all_states(num_fluents: int) -> tuple[tuple[bool, ...], ...]:
    states: list[tuple[bool, ...]] = []
    for mask in range(1 << num_fluents):
        state = tuple(bool((mask >> index) & 1) for index in range(num_fluents))
        states.append(state)
    return tuple(states)


def preconditions_hold(
    state: tuple[bool, ...], preconditions: tuple[Literal, ...], index_by_fluent: dict[str, int]
) -> bool:
    return all(literal.holds_in(state, index_by_fluent) for literal in preconditions)


def build_effect_index(
    effect_statements: tuple[EffectStatement, ...]
) -> dict[str, dict[str, list[EffectStatement]]]:
    indexed: dict[str, dict[str, list[EffectStatement]]] = {}
    for statement in effect_statements:
        action_map = indexed.setdefault(statement.action, {})
        action_map.setdefault(statement.literal.name, []).append(statement)
    return indexed


def build_transition_table(
    actions: tuple[str, ...],
    fluents: tuple[str, ...],
    states: tuple[tuple[bool, ...], ...],
    effect_statements: tuple[EffectStatement, ...],
    index_by_fluent: dict[str, int],
) -> dict[tuple[str, tuple[bool, ...]], tuple[bool, ...]]:
    effect_index = build_effect_index(effect_statements)
    transition_table: dict[tuple[str, tuple[bool, ...]], tuple[bool, ...]] = {}

    for action in actions:
        per_fluent_effects = effect_index.get(action, {})
        for state in states:
            next_state = list(state)
            for fluent in fluents:
                applicable_values: set[bool] = set()
                for statement in per_fluent_effects.get(fluent, []):
                    if preconditions_hold(state, statement.preconditions, index_by_fluent):
                        applicable_values.add(statement.literal.positive)
                if len(applicable_values) > 1:
                    raise ParseError(
                        f"Contradictory effects detected for action '{action}' and fluent '{fluent}'."
                    )
                if applicable_values:
                    next_state[index_by_fluent[fluent]] = next(iter(applicable_values))
            transition_table[(action, state)] = tuple(next_state)

    return transition_table


def build_cost_table(
    actions: tuple[str, ...],
    states: tuple[tuple[bool, ...], ...],
    transition_table: dict[tuple[str, tuple[bool, ...]], tuple[bool, ...]],
    cost_statements: tuple[CostStatement, ...],
) -> dict[tuple[str, tuple[bool, ...]], int]:
    declared_costs: dict[str, int] = {}
    for statement in cost_statements:
        previous = declared_costs.get(statement.action)
        if previous is not None and previous != statement.cost:
            raise ParseError(
                f"Conflicting costs declared for action '{statement.action}' at line {statement.source_line}."
            )
        declared_costs[statement.action] = statement.cost

    cost_table: dict[tuple[str, tuple[bool, ...]], int] = {}
    for action in actions:
        declared_cost = declared_costs.get(action)
        for state in states:
            next_state = transition_table[(action, state)]
            cost_table[(action, state)] = declared_cost if declared_cost and next_state != state else 0
    return cost_table


def run_program(
    program: tuple[str, ...],
    start_state: tuple[bool, ...],
    transition_table: dict[tuple[str, tuple[bool, ...]], tuple[bool, ...]],
    cost_table: dict[tuple[str, tuple[bool, ...]], int],
) -> tuple[tuple[bool, ...], int]:
    state = start_state
    total_cost = 0
    for action in program:
        total_cost += cost_table[(action, state)]
        state = transition_table[(action, state)]
    return state, total_cost


def state_satisfies(state: tuple[bool, ...], literals: tuple[Literal, ...], index_by_fluent: dict[str, int]) -> bool:
    return all(literal.holds_in(state, index_by_fluent) for literal in literals)


def determine_models(domain: Domain, context: EvaluationContext) -> tuple[tuple[bool, ...], ...]:
    models: list[tuple[bool, ...]] = []
    initial_constraints = tuple(statement for statement in domain.value_statements if not statement.program)
    transition_constraints = tuple(statement for statement in domain.value_statements if statement.program)

    for state in context.states:
        if not state_satisfies(
            state,
            tuple(statement.literal for statement in initial_constraints),
            context.index_by_fluent,
        ):
            continue

        consistent = True
        for statement in transition_constraints:
            final_state, _ = run_program(
                statement.program,
                state,
                context.transition_table,
                context.cost_table,
            )
            if not statement.literal.holds_in(final_state, context.index_by_fluent):
                consistent = False
                break
        if consistent:
            models.append(state)

    return tuple(models)


def evaluate_domain(domain: Domain) -> EvaluationContext:
    fluents, actions = gather_signature(domain)
    index_by_fluent = {fluent: index for index, fluent in enumerate(fluents)}
    states = all_states(len(fluents))
    transition_table = build_transition_table(
        actions,
        fluents,
        states,
        domain.effect_statements,
        index_by_fluent,
    )
    cost_table = build_cost_table(actions, states, transition_table, domain.cost_statements)
    context = EvaluationContext(
        fluents=fluents,
        actions=actions,
        states=states,
        transition_table=transition_table,
        cost_table=cost_table,
        models=(),
        index_by_fluent=index_by_fluent,
    )
    context.models = determine_models(domain, context)
    return context


def evaluate_query(query: Query, context: EvaluationContext) -> QueryStatus:
    if not context.models:
        return QueryStatus.UNDEFINED

    if isinstance(query, GoalQuery):
        for model in context.models:
            final_state, _ = run_program(query.program, model, context.transition_table, context.cost_table)
            if not state_satisfies(final_state, query.goal, context.index_by_fluent):
                return QueryStatus.FALSE
        return QueryStatus.TRUE

    if isinstance(query, MaxCostQuery):
        for model in context.models:
            _, total_cost = run_program(query.program, model, context.transition_table, context.cost_table)
            if total_cost > query.cost:
                return QueryStatus.FALSE
        return QueryStatus.TRUE

    for model in context.models:
        _, total_cost = run_program(query.program, model, context.transition_table, context.cost_table)
        if total_cost != query.cost:
            return QueryStatus.FALSE
    return QueryStatus.TRUE


def format_state(state: tuple[bool, ...], fluents: tuple[str, ...]) -> str:
    rendered = [name if value else f"!{name}" for name, value in zip(fluents, state, strict=True)]
    return "{" + ", ".join(rendered) + "}"


def format_editor_line_number(line_number: int, width: int | None = None) -> str:
    prompt = f"{line_number}: "
    return prompt if width is None else prompt.rjust(width)


def interactive_editor_banner_lines() -> tuple[str, ...]:
    return (
        "Press F5 or Ctrl+R to run.",
        "Format:",
        "[domain]",
        "...",
        "[queries]",
        "...",
        "",
        "-" * 32,
        "",
    )


def read_prompt_toolkit_spec_lines() -> list[SourceLine]:
    for line in interactive_editor_banner_lines():
        print(line)

    bindings = KeyBindings()

    @bindings.add("f5")
    @bindings.add("c-r")
    def submit_buffer(event) -> None:
        event.app.exit(result=event.current_buffer.text)

    session = PromptSession(multiline=True)
    try:
        raw_text = session.prompt(
            format_editor_line_number(1),
            key_bindings=bindings,
            prompt_continuation=lambda width, line_number, is_soft_wrap: (
                " " * width
                if is_soft_wrap
                else format_editor_line_number(line_number + 1, width)
            ),
        )
    except EOFError as exc:
        raise ParseError("Interactive input ended before the specification was submitted.") from exc
    except KeyboardInterrupt as exc:
        raise ParseError("Interactive input was cancelled.") from exc

    return collect_source_lines(raw_text.splitlines())


def read_interactive_spec_lines() -> list[SourceLine]:
    if sys.stdin.isatty() and sys.stdout.isatty():
        return read_prompt_toolkit_spec_lines()

    raw_text = sys.stdin.read()
    if not raw_text.strip():
        raise ParseError("Interactive input ended before the specification was submitted.")
    return collect_source_lines(raw_text.splitlines())


def load_interactive_inputs() -> LoadedInputs:
    domain, queries = parse_spec_lines(
        read_interactive_spec_lines(),
        "<interactive>",
        require_explicit_sections=True,
    )
    return LoadedInputs(domain=domain, queries=queries, interactive_session=True)


def load_inputs(args: argparse.Namespace) -> LoadedInputs:
    if args.interactive:
        return load_interactive_inputs()

    if args.spec_file:
        domain, queries = parse_spec_file(Path(args.spec_file))
        if args.domain_file or args.query_file:
            raise ParseError("Use either a combined spec file or separate --domain-file/--query-file inputs.")
        return LoadedInputs(domain=domain, queries=queries, interactive_session=False)

    if not sys.stdin.isatty():
        piped_text = sys.stdin.read()
        domain, queries = parse_spec_text(piped_text)
        return LoadedInputs(domain=domain, queries=queries, interactive_session=False)

    if not args.domain_file:
        return load_interactive_inputs()

    domain = parse_domain_lines(read_lines(Path(args.domain_file)))
    queries = parse_query_lines(read_lines(Path(args.query_file))) if args.query_file else ()
    return LoadedInputs(domain=domain, queries=queries, interactive_session=False)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile and evaluate DS4 action-cost."
    )
    parser.add_argument(
        "spec_file",
        nargs="?",
        help="Combined spec file with optional [domain] and [queries] sections.",
    )
    parser.add_argument("--domain-file", help="Domain file when domain and queries are split.")
    parser.add_argument("--query-file", help="Query file when domain and queries are split.")
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Read domain and queries from keyboard prompts instead of files.",
    )
    parser.add_argument(
        "--show-models",
        action="store_true",
        help="Print every model (initial completion) accepted by the domain.",
    )
    return parser


def should_pause_on_exit(
    args: argparse.Namespace,
    interactive_session: bool = False,
    *,
    stdin_isatty: bool | None = None,
    stdout_isatty: bool | None = None,
) -> bool:
    input_is_tty = sys.stdin.isatty() if stdin_isatty is None else stdin_isatty
    output_is_tty = sys.stdout.isatty() if stdout_isatty is None else stdout_isatty
    if not (input_is_tty and output_is_tty):
        return False

    return interactive_session or args.interactive or (not args.spec_file and not args.domain_file)


def pause_before_exit() -> None:
    try:
        input("Press Enter to exit...")
    except EOFError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    try:
        loaded = load_inputs(args)
        context = evaluate_domain(loaded.domain)
        validate_queries(loaded.queries, context.fluents, context.actions)
    except (OSError, ParseError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if should_pause_on_exit(args):
            pause_before_exit()
        return 1

    if not context.models:
        print("DOMAIN STATUS: inconsistent (no model satisfies all value/effect statements)")

    if args.show_models and context.models:
        for index, model in enumerate(context.models, start=1):
            print(f"MODEL {index}: {format_state(model, context.fluents)}")

    if loaded.queries:
        for index, query in enumerate(loaded.queries, start=1):
            if isinstance(query, GoalQuery):
                rendered = query.render()
            elif isinstance(query, MaxCostQuery):
                rendered = query.render()
            else:
                rendered = query.render()
            print(f"QUERY {index}: {rendered}")
            print(f"RESULT {index}: {evaluate_query(query, context).value}")
    elif context.models:
        print("No queries provided.")

    if should_pause_on_exit(args, interactive_session=loaded.interactive_session):
        pause_before_exit()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
