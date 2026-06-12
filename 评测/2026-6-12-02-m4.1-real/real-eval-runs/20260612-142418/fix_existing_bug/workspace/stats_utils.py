def average(nums):
    if not nums:
        raise ValueError("nums must not be empty")
    return sum(nums) / len(nums)
