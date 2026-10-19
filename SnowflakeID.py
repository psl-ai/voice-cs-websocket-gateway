import time

class SnowflakeID:
    def __init__(self, machine_id):
        self.machine_id = machine_id
        self.sequence = 0
        self.last_timestamp = -1

        # 机器ID最大值限制
        if self.machine_id < 0 or self.machine_id > 1023:
            raise ValueError("Machine ID must be between 0 and 1023")

    def _current_timestamp(self):
        return int(time.time() * 1000)

    def generate_id(self):
        timestamp = self._current_timestamp()

        # 当前时间戳与上一次生成的ID时间相同
        if timestamp == self.last_timestamp:
            self.sequence = (self.sequence + 1) & 0xFFF  # 序列号递增，最多4095
            if self.sequence == 0:
                while timestamp <= self.last_timestamp:
                    timestamp = self._current_timestamp()
        else:
            self.sequence = 0

        self.last_timestamp = timestamp

        # 计算ID
        snowflake_id = ((timestamp << 22) |
                        (self.machine_id << 12) |
                        self.sequence)

        return snowflake_id