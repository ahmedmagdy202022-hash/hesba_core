from decimal import Decimal


def recipe_requirements(recipe, planned_output_quantity):
    if planned_output_quantity <= 0:
        raise ValueError("Planned output quantity must be greater than zero.")
    factor = Decimal(planned_output_quantity) / recipe.output_quantity
    rows = []
    for line in recipe.lines.select_related("component_item").all():
        base_quantity = line.quantity * factor
        scrap_quantity = base_quantity * (line.scrap_percent or Decimal("0")) / Decimal("100")
        rows.append({
            "item": line.component_item,
            "base_quantity": base_quantity,
            "scrap_quantity": scrap_quantity,
            "required_quantity": base_quantity + scrap_quantity,
        })
    return rows
