def calculate_bmi(weight_kg, height_cm, age):
    height_m = height_cm / 100.0
    bmi = weight_kg / (height_m ** 2)
    
    if bmi < 18.5:
        category = "Underweight"
    elif 18.5 <= bmi < 25.0:
        category = "Normal weight"
    elif 25.0 <= bmi < 30.0:
        category = "Overweight"
    else:
        category = "Obese"
        
    min_ideal_weight = 18.5 * (height_m ** 2)
    max_ideal_weight = 24.9 * (height_m ** 2)
    
    return bmi, category, min_ideal_weight, max_ideal_weight


def main():
    print("=== BMI & Ideal Weight Calculator ===")
    age = int(input("Enter your age: "))
    weight = float(input("Enter your weight in kg: "))
    height = float(input("Enter your height in cm: "))
    
    bmi, category, min_weight, max_weight = calculate_bmi(weight, height, age)
    
    print("\n--- Results ---")
    print(f"Age: {age} years")
    print(f"BMI Value: {bmi:.2f}")
    print(f"Category: {category}")
    print(f"Ideal Weight Range: {min_weight:.1f} kg - {max_weight:.1f} kg")


if __name__ == "__main__":
    main()
