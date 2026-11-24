import  random
i = 1
a = random.randint(0,100)
b = int(input("输入数字: \n"))
while a !=b :
    if a > b:
        try:
            print("小")
            b = int(input("输入数字:"))
        except ValueError as e :
            print("输入的不是数字")
    else:
        try:
            print("大了")
            b = int(input("输入数字:"))
        except ValueError as e :
            print("输入的不是数字")
    i = i + 1
print("预测{}次，你猜中啦".format(i))