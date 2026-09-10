"""
BMI and Ideal Weight Calculator
-------------------------------
Created by AI Assistant.

This program takes multiple inputs (Weight, Height, Age) and computes
multiple outputs (BMI score, WHO Category, Ideal Weight Range).
"""

def calculate_bmi_metrics(weight_kg: float, height_cm: float, age: int):
    """
    Calculates BMI, weight category according to WHO guidelines, 
    and recommended healthy weight range for a given height.
    """
    height_m = height_cm / 100.0
    bmi = weight_kg / (height_m ** 2)
    
    # Determine weight category based on standard BMI thresholds
    if bmi < 18.5:
        category = "Underweight"
    elif 18.5 <= bmi < 24.9:
        category = "Normal weight"
    elif 25.0 <= bmi < 29.9:
        category = "Overweight"
    else:
        category = "Obese"

    # Calculate healthy weight range (BMI 18.5 - 24.9)
    min_healthy_weight = 18.5 * (height_m ** 2)
    max_healthy_weight = 24.9 * (height_m ** 2)

    return {
        "age": age,
        "bmi": round(bmi, 2),
        "category": category,
        "min_healthy_weight": round(min_healthy_weight, 1),
        "max_healthy_weight": round(max_healthy_weight, 1)
    }


def main():
    print("=" * 45)
    print("      BMI & HEALTHY WEIGHT CALCULATOR (AI)   ")
    print("=" * 45)

    try:
        age = int(input("Enter age (years): "))
        weight = float(input("Enter weight (kg): "))
        height = float(input("Enter height (cm): "))

        if weight <= 0 or height <= 0 or age <= 0:
            print("[Error] Weight, height, and age must be positive values.")
            return

        results = calculate_bmi_metrics(weight, height, age)

        print("\n" + "-" * 45)
        print("                 CALCULATED RESULTS          ")
        print("-" * 45)
        print(f" User Age           : {results['age']} years")
        print(f" Body Mass Index    : {results['bmi']} kg/m²")
        print(f" Weight Category    : {results['category']}")
        print(f" Ideal Weight Range : {results['min_healthy_weight']} kg - {results['max_healthy_weight']} kg")
        print("-" * 45)

    except ValueError:
        print("[Error] Invalid input. Please enter numerical values for age, weight, and height.")


if __name__ == "__main__":
    main()
