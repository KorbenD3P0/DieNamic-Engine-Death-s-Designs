"""
Hazard Chain Analyzer
Analyzes hazard data to identify dependencies and chain reactions.
"""
from typing import Dict, List, Set, Tuple


def analyze_hazard_chains(resource_manager) -> Dict[str, Set[str]]:
    """
    Analyze all hazards and build a dependency map.
    
    Returns:
        Dict mapping hazard_id -> set of hazards it can trigger
    """
    hazards_data = resource_manager.get_data('hazards', {})
    dependencies = {}
    
    for hazard_id, hazard_def in hazards_data.items():
        triggered_hazards = set()
        
        # Check all states for hazard triggers
        states = hazard_def.get('states', {})
        for state_name, state_data in states.items():
            # Check triggers_hazard_on_state_change
            triggers = state_data.get('triggers_hazard_on_state_change', [])
            for trigger in triggers:
                if isinstance(trigger, dict):
                    hazard_type = trigger.get('type')
                    if hazard_type:
                        triggered_hazards.add(hazard_type)
        
        if triggered_hazards:
            dependencies[hazard_id] = triggered_hazards
    
    return dependencies


def check_missing_dependencies(
    selected_hazards: List[str],
    resource_manager
) -> List[Tuple[str, Set[str]]]:
    """
    Check if selected hazards have unselected dependencies.
    NOW RECURSIVE: Follows entire chain (A → B → C → D).
    
    Returns:
        List of (hazard_id, ALL_missing_dependencies) tuples
    """
    chains = analyze_hazard_chains(resource_manager)
    selected_set = set(selected_hazards)
    missing = []
    
    def get_all_dependencies(hazard_id: str, visited: Set[str] = None) -> Set[str]:
        """Recursively get ALL transitive dependencies for a hazard."""
        if visited is None:
            visited = set()
        
        # Prevent infinite loops
        if hazard_id in visited:
            return set()
        visited.add(hazard_id)
        
        all_deps = set()
        
        # Get direct dependencies
        if hazard_id in chains:
            direct_deps = chains[hazard_id]
            all_deps.update(direct_deps)
            
            # Recursively get dependencies of dependencies
            for dep in direct_deps:
                transitive_deps = get_all_dependencies(dep, visited.copy())
                all_deps.update(transitive_deps)
        
        return all_deps
    
    for hazard in selected_hazards:
        # Get ALL dependencies (direct + transitive)
        all_deps = get_all_dependencies(hazard)
        
        # Find which ones are missing
        unselected = all_deps - selected_set
        
        if unselected:
            missing.append((hazard, unselected))
    
    return missing


def format_chain_warning(missing_deps: List[Tuple[str, Set[str]]]) -> str:
    """
    Format a user-friendly warning message about missing hazard chains.
    NOW SHOWS FULL CHAINS, not just immediate triggers.
    """
    if not missing_deps:
        return ""
    
    lines = ["[color=ffaa00][b]INCOMPLETE HAZARD CHAINS DETECTED[/b][/color]\n"]
    lines.append("The following hazards have chain reactions that won't fully execute:\n")
    
    for hazard, deps in missing_deps:
        hazard_name = hazard.replace('_', ' ').title()
        
        if len(deps) == 1:
            # Simple single dependency
            dep_name = list(deps)[0].replace('_', ' ').title()
            lines.append(f"• [b]{hazard_name}[/b] → triggers [color=ff6666]{dep_name}[/color]")
        else:
            # Multiple dependencies (could be a chain)
            lines.append(f"• [b]{hazard_name}[/b] → triggers:")
            for dep in sorted(deps):
                dep_name = dep.replace('_', ' ').title()
                lines.append(f"    - [color=ff6666]{dep_name}[/color]")
    
    lines.append("\n[color=888888]Add these hazards to test complete chains.[/color]")
    lines.append("[color=888888]Or proceed anyway if testing partial interactions.[/color]")
    
    return "\n".join(lines)
