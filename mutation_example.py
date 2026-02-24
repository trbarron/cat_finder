# --------------------- #
#      Example One      #
# --------------------- #

## function

def square(x):
    ans = x * x
    return ans

## Test

assert square(2) == 4


# --------------------- #

## Mutation

def square_mut_1(x):
    ans = x + x # hey this is a + instead of a *
    return ans

assert square(2) == 4

# // This is a surviving mutation because it passes the test
# // Lets kill it. 


# --------------------- #
#      Example Two      #
# --------------------- #

## function

def square_plus_one(x):
    ans = square(x) + 1
    return ans

assert square_plus_one(2) == 5


# --------------------- #

## Mutation

def square_plus_one_mut_1(x):
    ans = square(x) + 2 # hey this is a + 2 instead of a + 1
    return ans

def square_plus_one_mut_2(x):
    ans = square(None) + 1 # hey this is a None instead of a x
    return ans

def square_plus_one_mut_3(x):
    ans = square(1) + 1 # hey this is a 1 instead of a x
    return ans

assert square_plus_one(2) == 5

