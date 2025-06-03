#include <iostream>


class Dog {
public:
    // Constructor
    Dog(std::string name, int age);

    // Methods
    void bark() const;
    int getAge() const;
    void setAge(int age);
    std::string getName() const{
    return name;
}

private:
    // Attributes
    std::string name;
    int age;
};


template <typename T>
class MyTemplateClass {
public:
    T add(T a, T b); // Declaration of the template function
};

// Definition of the template function outside the class
template <typename T>
T MyTemplateClass<T>::add(T a, T b) {
    return a + b;
}



// Definition of the Dog class (as shown above)
Dog::Dog(std::string name, int age) : name(name), age(age) {}

void Dog::bark() const {
    std::cout << "Woof!" << std::endl;
}

int Dog::getAge() const {
    return age;
}

void Dog::setAge(int age) {
    this->age = age;
}


// Template function declaration
template <typename T>
T add(T a, T b);

int main() {
    // Using the template function with integers
    int sum_int = add(5, 3);
    std::cout << "Sum of integers: " << sum_int << std::endl;

    // Using the template function with doubles
    double sum_double = add(2.5, 1.7);
    std.cout << "Sum of doubles: " << sum_double << std::endl;

    return 0;
}

// Template function definition
template <typename T>
T add(T a, T b) {
    return a + b;
}