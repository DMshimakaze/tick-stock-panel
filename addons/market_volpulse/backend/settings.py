"""Configuration sent by the page and validated by the single background owner."""
from dataclasses import asdict, dataclass

from .energy import Config


@dataclass(frozen=True)
class Settings(Config):
    refresh_seconds: int = 5
    notifications: bool = False

    def __post_init__(self):
        super().__post_init__()
        if type(self.refresh_seconds) is not int or not 3 <= self.refresh_seconds <= 15:
            raise ValueError('刷新间隔须为 3–15 秒')
        if type(self.notifications) is not bool:
            raise ValueError('通知开关无效')

    def public(self):
        return {key: value for key, value in asdict(self).items() if key != 'prior'}

    @classmethod
    def from_command(cls, values):
        if not isinstance(values, dict) or values.keys() != cls().public().keys():
            raise ValueError('设置字段不匹配')
        return cls(**values)
