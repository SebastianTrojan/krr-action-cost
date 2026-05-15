from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, TypeVar

from prompt_toolkit.application import Application
from prompt_toolkit.application.run_in_terminal import run_in_terminal
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import ConditionalContainer, Float, FloatContainer
from prompt_toolkit.layout import HSplit, Layout, VSplit
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.widgets import Frame, TextArea


IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
INCONSISTENT_DOMAIN_MESSAGE = "DOMAIN STATUS: inconsistent (no model satisfies all value/effect statements)"


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


SectionItem = TypeVar("SectionItem")


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


def parse_literal(text: str, line_number: int) -> Literal:
    token = text.strip()
    positive = True
    if token.startswith("!"):
        positive = False
        token = token[1:].strip()
    name = parse_identifier(token, line_number, "fluent")
    return Literal(name=name, positive=positive)


def parse_literal_list(text: str, line_number: int) -> tuple[Literal, ...]:
    cleaned = unwrap_enclosure(text, "{", "}")
    cleaned = unwrap_enclosure(cleaned, "(", ")")
    if not cleaned:
        return ()
    parts = [part.strip() for part in cleaned.split(",") if part.strip()]
    if not parts:
        return ()
    return tuple(parse_literal(part, line_number) for part in parts)


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


def split_section_items(
    items: list[SectionItem],
    source_name: str,
    *,
    text_getter: Callable[[SectionItem], str],
    require_explicit_sections: bool = False,
    require_nonempty_sections: bool | None = None,
) -> tuple[list[SectionItem], list[SectionItem]]:
    if require_nonempty_sections is None:
        require_nonempty_sections = require_explicit_sections

    domain_items: list[SectionItem] = []
    query_items: list[SectionItem] = []
    current_section = "domain" if not require_explicit_sections else ""
    saw_domain_header = False
    saw_queries_header = False

    for item in items:
        section_text = text_getter(item)
        lowered = section_text.lower()
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
            if section_text:
                raise ParseError(f"{source_name}: missing [domain] section before the first statement.")
            continue

        if current_section == "domain":
            domain_items.append(item)
        else:
            query_items.append(item)

    if require_explicit_sections:
        if not saw_domain_header:
            raise ParseError(f"{source_name}: missing [domain] section.")
        if not saw_queries_header:
            raise ParseError(f"{source_name}: missing [queries] section.")
        if require_nonempty_sections and not any(text_getter(item) for item in domain_items):
            raise ParseError(f"{source_name}: no domain statements found.")
        if require_nonempty_sections and not any(text_getter(item) for item in query_items):
            raise ParseError(f"{source_name}: no query statements found.")
    elif not any(text_getter(item) for item in domain_items) and (saw_domain_header or saw_queries_header):
        raise ParseError(f"{source_name}: no domain statements found.")

    return domain_items, query_items


def split_spec_lines(
    lines: list[SourceLine],
    source_name: str,
    require_explicit_sections: bool = False,
) -> tuple[list[SourceLine], list[SourceLine]]:
    return split_section_items(
        lines,
        source_name,
        text_getter=lambda line: line.text,
        require_explicit_sections=require_explicit_sections,
    )


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
    return parse_spec_text(path.read_text(encoding="utf-8"), str(path))


def parse_spec_text(text: str, source_name: str = "<stdin>") -> tuple[Domain, tuple[Query, ...]]:
    return parse_spec_lines(
        collect_source_lines(text.splitlines()),
        source_name,
        require_explicit_sections=True,
    )


def split_spec_text_for_editor(text: str, source_name: str) -> tuple[str, str]:
    domain_lines, query_lines = split_section_items(
        text.splitlines(),
        source_name,
        text_getter=normalize_line,
        require_explicit_sections=True,
        require_nonempty_sections=False,
    )

    return "\n".join(domain_lines).strip(), "\n".join(query_lines).strip()


def load_editor_texts_from_spec_path(path: Path) -> tuple[str, str]:
    return split_spec_text_for_editor(path.read_text(encoding="utf-8"), str(path))


def add_error_context(source_name: str, exc: ParseError) -> ParseError:
    return ParseError(f"{source_name}: {exc}")


def parse_domain_text(text: str, source_name: str = "<domain>") -> Domain:
    lines = collect_source_lines(text.splitlines())
    if not lines:
        raise ParseError(f"{source_name}: no domain statements found.")
    try:
        return parse_domain_lines(lines)
    except ParseError as exc:
        raise add_error_context(source_name, exc) from exc


def parse_queries_text(text: str, source_name: str = "<queries>") -> tuple[Query, ...]:
    lines = collect_source_lines(text.splitlines())
    if not lines:
        return ()
    try:
        return parse_query_lines(lines)
    except ParseError as exc:
        raise add_error_context(source_name, exc) from exc


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
            continue

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
    state: tuple[bool, ...],
    preconditions: tuple[Literal, ...],
    index_by_fluent: dict[str, int],
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


def state_satisfies(
    state: tuple[bool, ...],
    literals: tuple[Literal, ...],
    index_by_fluent: dict[str, int],
) -> bool:
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


def render_context_output(
    context: EvaluationContext,
    queries: tuple[Query, ...],
    *,
    show_models: bool = False,
) -> str:
    if not context.models:
        return INCONSISTENT_DOMAIN_MESSAGE

    lines: list[str] = []

    if show_models:
        for index, model in enumerate(context.models, start=1):
            lines.append(f"MODEL {index}: {format_state(model, context.fluents)}")

    if queries:
        for index, query in enumerate(queries, start=1):
            lines.append(f"QUERY {index}: {query.render()}")
            lines.append(f"RESULT {index}: {evaluate_query(query, context).value}")
    else:
        lines.append("No queries provided.")

    return "\n".join(lines)


def evaluate_inputs(
    domain: Domain,
    queries: tuple[Query, ...],
    *,
    show_models: bool = False,
    domain_source_name: str | None = None,
    query_source_name: str | None = None,
) -> str:
    try:
        context = evaluate_domain(domain)
    except ParseError as exc:
        if domain_source_name is None:
            raise
        raise add_error_context(domain_source_name, exc) from exc

    try:
        validate_queries(queries, context.fluents, context.actions)
    except ParseError as exc:
        if query_source_name is None:
            raise
        raise add_error_context(query_source_name, exc) from exc

    return render_context_output(context, queries, show_models=show_models)


def render_evaluation_output(
    domain: Domain,
    queries: tuple[Query, ...],
    *,
    show_models: bool = False,
) -> str:
    return evaluate_inputs(domain, queries, show_models=show_models)


def evaluate_text_fragments(
    domain_text: str,
    queries_text: str,
    *,
    show_models: bool = False,
) -> str:
    domain = parse_domain_text(domain_text, source_name="Domain window")
    queries = parse_queries_text(queries_text, source_name="Queries window")
    return evaluate_inputs(
        domain,
        queries,
        show_models=show_models,
        domain_source_name="Domain window",
        query_source_name="Queries window",
    )


def interactive_status_text() -> str:
    return "F5/Ctrl+R - Run   Esc/Ctrl+Q - Exit   F1 - Help   Ctrl+O - Open file"


def interactive_help_text() -> str:
    return "\n".join(
        (
            "Enter domain statements in the left pane.",
            "Enter query statements in the right pane.",
            "",
            "Domain pane syntax:",
            "  Value statements:",
            "    fluent after action_1, action_2, ...",
            "  Initial statements:",
            "    initially fluent, !fluent, ...",
            "  Effect statements:",
            "    action causes fluent if fluent, !fluent, ...",
            "    action causes fluent, !fluent if fluent, !fluent, ...",
            "  Cost statements:",
            "    action costs 5",
            "",
            "Queries pane syntax:",
            "  Goal queries:",
            "    fluent after action_1, action_2, ...",
            "    fluent, !fluent after action_1, action_2, ...",
            "  Cost bound queries:",
            "    action_1, action_2 executable with cost 5",
            "  Exact cost queries:",
            "    action_1, action_2 executable with exact cost 5",
            "",
            "Domain input examples:",
            "  initially !doorOpen, hasKey",
            "  openDoor causes doorOpen if hasKey",
            "  openDoor causes doorOpen, !alarmOn if hasKey",
            "  openDoor costs 5",
            "  doorOpen after openDoor",
            "",
            "Query input examples:",
            "  doorOpen after openDoor",
            "  doorOpen, !alarmOn after openDoor",
            "  openDoor executable with cost 5",
            "  openDoor executable with exact cost 5",
            "",
            "Opened file structure for Ctrl+O:",
            "  [domain]",
            "  ... domain statements ...",
            "  [queries]",
            "  ... query statements ...",
            "",
            "Shortcuts:",
            "  Ctrl+O: open combined .krr or .txt spec file",
            "  Tab / Shift+Tab: switch between Domain and Queries",
            "  F5 / Ctrl+R: run compiler",
            "  Esc / Ctrl+Q: exit",
            "Press F1 again to hide this help.",
        )
    )


def choose_interactive_spec_file() -> str:
    from tkinter import Tk, filedialog

    root = Tk()
    root.withdraw()
    root.update_idletasks()
    root.attributes("-topmost", True)
    try:
        return filedialog.askopenfilename(
            title="Open spec file",
            filetypes=[
                ("KRR or text files", "*.krr *.txt"),
                ("All files", "*.*"),
            ],
        )
    finally:
        root.destroy()


def load_interactive_workspace_texts(args: argparse.Namespace) -> tuple[str, str]:
    validate_input_source_selection(args)

    if args.spec_file:
        spec_path = Path(args.spec_file)
        return load_editor_texts_from_spec_path(spec_path)

    domain_text = ""
    query_text = ""
    if args.domain_file:
        domain_text = Path(args.domain_file).read_text(encoding="utf-8").strip()
    if args.query_file:
        query_text = Path(args.query_file).read_text(encoding="utf-8").strip()
    return domain_text, query_text


def run_interactive_workspace(
    *,
    show_models: bool = False,
    initial_domain_text: str = "",
    initial_queries_text: str = "",
) -> int:
    domain_area = TextArea(
        text=initial_domain_text,
        multiline=True,
        scrollbar=True,
        line_numbers=True,
        wrap_lines=False,
        focus_on_click=True,
    )
    query_area = TextArea(
        text=initial_queries_text,
        multiline=True,
        scrollbar=True,
        line_numbers=True,
        wrap_lines=False,
        focus_on_click=True,
    )
    output_area = TextArea(
        text="",
        multiline=True,
        scrollbar=True,
        wrap_lines=True,
        read_only=True,
        focusable=False,
    )
    status_area = TextArea(
        text=interactive_status_text(),
        multiline=False,
        wrap_lines=False,
        read_only=True,
        focusable=False,
        dont_extend_height=True,
        height=1,
    )
    help_area = TextArea(
        text=interactive_help_text(),
        multiline=True,
        wrap_lines=True,
        scrollbar=True,
        read_only=True,
        focusable=True,
        focus_on_click=True,
        dont_extend_height=True,
        height=18,
    )
    show_help = {"value": False}

    help_panel = ConditionalContainer(
        content=Frame(help_area, title="Help"),
        filter=Condition(lambda: show_help["value"]),
    )

    def run_current_buffers() -> None:
        try:
            output_area.text = evaluate_text_fragments(
                domain_area.text,
                query_area.text,
                show_models=show_models,
            )
        except (OSError, ParseError) as exc:
            output_area.text = f"ERROR: {exc}"

    bindings = KeyBindings()

    @bindings.add("f5")
    @bindings.add("c-r")
    def run_compiler(_event) -> None:
        run_current_buffers()

    @bindings.add("c-o")
    def open_file(event) -> None:
        async def choose_and_load() -> None:
            try:
                selected_path = await run_in_terminal(choose_interactive_spec_file, in_executor=True)
            except Exception as exc:
                output_area.text = f"ERROR: {exc}"
                event.app.invalidate()
                return

            if not selected_path:
                event.app.invalidate()
                return

            try:
                domain_text, query_text = load_editor_texts_from_spec_path(Path(selected_path))
            except (OSError, ParseError) as exc:
                output_area.text = f"ERROR: {exc}"
            else:
                domain_area.text = domain_text
                query_area.text = query_text
                output_area.text = ""
                show_help["value"] = False
                event.app.layout.focus(domain_area)
            event.app.invalidate()

        event.app.create_background_task(choose_and_load())

    @bindings.add("f1")
    def toggle_help(event) -> None:
        show_help["value"] = not show_help["value"]
        if show_help["value"]:
            event.app.layout.focus(help_area)
        elif event.app.layout.has_focus(help_area):
            event.app.layout.focus(domain_area)
        event.app.invalidate()

    @bindings.add("tab")
    def focus_next(event) -> None:
        if event.app.layout.has_focus(domain_area):
            event.app.layout.focus(query_area)
        else:
            event.app.layout.focus(domain_area)

    @bindings.add("s-tab")
    def focus_previous(event) -> None:
        if event.app.layout.has_focus(query_area):
            event.app.layout.focus(domain_area)
        else:
            event.app.layout.focus(query_area)

    @bindings.add("escape")
    @bindings.add("c-q")
    def quit_editor(event) -> None:
        event.app.exit(result=None)

    main_container = HSplit(
        [
            status_area,
            VSplit(
                [
                    Frame(domain_area, title="Domain"),
                    Frame(query_area, title="Queries"),
                ],
                padding=1,
            ),
            Frame(
                output_area,
                title="Output",
                height=Dimension(min=8, preferred=10),
            ),
        ],
        padding=1,
    )

    root_container = FloatContainer(
        content=main_container,
        floats=[
            Float(
                content=help_panel,
                top=1,
                bottom=1,
                left=2,
                right=2,
                z_index=10,
            )
        ],
    )

    app = Application(
        layout=Layout(root_container, focused_element=domain_area),
        key_bindings=bindings,
        full_screen=True,
        mouse_support=True,
    )
    app.run()
    return 0


def load_interactive_inputs_from_stream() -> LoadedInputs:
    raw_text = sys.stdin.read()
    if not raw_text.strip():
        raise ParseError("Interactive input ended before the specification was submitted.")
    domain, queries = parse_spec_text(raw_text, "<interactive>")
    return LoadedInputs(domain=domain, queries=queries)


def validate_input_source_selection(args: argparse.Namespace) -> None:
    if args.spec_file and (args.domain_file or args.query_file):
        raise ParseError("Use either a combined spec file or separate --domain-file/--query-file inputs.")


def load_inputs(args: argparse.Namespace) -> LoadedInputs:
    validate_input_source_selection(args)

    if args.interactive:
        return load_interactive_inputs_from_stream()

    if args.spec_file:
        domain, queries = parse_spec_file(Path(args.spec_file))
        return LoadedInputs(domain=domain, queries=queries)

    if not sys.stdin.isatty():
        piped_text = sys.stdin.read()
        domain, queries = parse_spec_text(piped_text)
        return LoadedInputs(domain=domain, queries=queries)

    if args.domain_file:
        domain = parse_domain_lines(read_lines(Path(args.domain_file)))
        queries = parse_query_lines(read_lines(Path(args.query_file))) if args.query_file else ()
        return LoadedInputs(domain=domain, queries=queries)

    raise ParseError("No input provided.")


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compile and evaluate DS4 action-cost.")
    parser.add_argument(
        "spec_file",
        nargs="?",
        help="Combined spec file with [domain] and [queries] sections.",
    )
    parser.add_argument("--domain-file", help="Domain file when domain and queries are split.")
    parser.add_argument("--query-file", help="Query file when domain and queries are split.")
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Open the interactive editor instead of reading from files.",
    )
    parser.add_argument(
        "--show-models",
        action="store_true",
        help="Print every model (initial completion) accepted by the domain.",
    )
    return parser


def uses_interactive_entrypoint(args: argparse.Namespace) -> bool:
    return args.interactive or (not args.spec_file and not args.domain_file)


def should_use_interactive_workspace(args: argparse.Namespace) -> bool:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return False
    return uses_interactive_entrypoint(args)


def should_pause_on_exit(
    args: argparse.Namespace,
    *,
    stdin_isatty: bool | None = None,
    stdout_isatty: bool | None = None,
) -> bool:
    input_is_tty = sys.stdin.isatty() if stdin_isatty is None else stdin_isatty
    output_is_tty = sys.stdout.isatty() if stdout_isatty is None else stdout_isatty
    if not (input_is_tty and output_is_tty):
        return False

    return uses_interactive_entrypoint(args)


def pause_before_exit() -> None:
    try:
        input("Press Enter to exit...")
    except EOFError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    if should_use_interactive_workspace(args):
        try:
            initial_domain_text, initial_queries_text = load_interactive_workspace_texts(args)
            return run_interactive_workspace(
                show_models=args.show_models,
                initial_domain_text=initial_domain_text,
                initial_queries_text=initial_queries_text,
            )
        except (OSError, ParseError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    try:
        loaded = load_inputs(args)
        output = render_evaluation_output(
            loaded.domain,
            loaded.queries,
            show_models=args.show_models,
        )
    except (OSError, ParseError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if should_pause_on_exit(args):
            pause_before_exit()
        return 1

    if output:
        print(output)

    if should_pause_on_exit(args):
        pause_before_exit()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
