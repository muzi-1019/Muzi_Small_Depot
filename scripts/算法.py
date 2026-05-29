# 1.判断偶数（支持传入列表）
def is_even(data):
    li = []
    # 判断是不是列表
    if isinstance(data, list):
        for num in data:
            if num % 2 == 0:
                li.append(num)
    return li

# 2.判断奇数（支持传入列表）
def is_odd(data):
    li = []
    if isinstance(data, list):
        for num in data:
            if num % 2 == 1:
                li.append(num)
    return li

# 3.冒泡排序
def bubble_sort(arr):
    length = len(arr)
    for i in range(length):
        for j in range(length - i - 1):
            if arr[j] > arr[j + 1]:
                arr[j], arr[j + 1] = arr[j + 1], arr[j]
    return arr

# 4. 找最大值
def find_max(lst):
    max_num = lst[0]
    for n in lst:
        if n > max_num:
            max_num = n
    return max_num

# 5. 找最小值
def find_min(lst):
    min_num = lst[0]
    for n in lst:
        if n < min_num:
            min_num = n
    return min_num

# 6. 统计元素个数
def count_num(lst, target):
    count = 0
    for n in lst:
        if n == target:
            count += 1
    return count

# 7. 反转列表
def reverse_list(lst):
    new_lst = []
    for i in range(len(lst)-1, -1, -1):
        new_lst.append(lst[i])
    return new_lst

# 8. 求和算法
def sum_all(lst):
    total = 0
    for i in lst:
        total += i
    return total

# 9. 阶乘计算
def factorial(n):
    res = 1
    for i in range(1, n+1):
        res *= i
    return res

# 10. 累加 1~10
def add_one_to_n(n):
    s = 0
    for i in range(1, n+1):
        s += i
    return s

# 全部测试【严格对应上面1-10顺序】
nums = [1,2,3,4,5,6,7,8,9]
list1 = [5, 3, 8, 1, 2]

print("1.列表中的偶数：", is_even(nums))
print("2.列表中的奇数：", is_odd(nums))
print("3.冒泡排序结果：", bubble_sort(list1))
print("4.最大值：", find_max(nums))
print("5.最小值：", find_min(nums))
print("6.数字3出现次数：", count_num(nums, 3))
print("7.反转列表：", reverse_list(nums))
print("8.列表总和：", sum_all(nums))
print("9.5的阶乘：", factorial(5))
print("10.1到10累加和：", add_one_to_n(10))