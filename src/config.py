import os
import yaml
from pathlib import Path
from typing import Dict, Any
from dotenv import load_dotenv


class Config:
    """配置管理类"""
    
    def __init__(self, config_path: str = None):
        self.config_path = config_path or "config/settings.yaml"
        self.config = self._load_config()
        self._load_environment_variables()
    
    def _load_config(self) -> Dict[str, Any]:
        """加载 YAML 配置文件"""
        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            print(f"配置文件未找到: {self.config_path}，使用默认配置")
            return self._get_default_config()
        except Exception as e:
            print(f"加载配置文件失败: {e}")
            return self._get_default_config()
    
    def _get_default_config(self) -> Dict[str, Any]:
        """获取默认配置"""
        return {
            'app': {'name': 'Multi-omics Agent', 'version': '0.1.0'},
            'llm': {'provider': 'openai', 'model': 'gpt-4'},
            'knowledge': {'lightrag': {'working_dir': './knowledge_base'}},
        }
    
    def _load_environment_variables(self):
        """加载环境变量"""
        load_dotenv()
        
        # 替换配置中的环境变量
        self._replace_env_vars(self.config)
    
    def _replace_env_vars(self, config: Dict[str, Any]):
        """递归替换配置中的环境变量"""
        for key, value in config.items():
            if isinstance(value, str) and value.startswith('${') and value.endswith('}'):
                env_var = value[2:-1]
                config[key] = os.getenv(env_var, value)
            elif isinstance(value, dict):
                self._replace_env_vars(value)
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置值"""
        keys = key.split('.')
        value = self.config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value
    
    def set(self, key: str, value: Any):
        """设置配置值"""
        keys = key.split('.')
        config = self.config
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        config[keys[-1]] = value
    
    def save(self, config_path: str = None):
        """保存配置到文件"""
        save_path = config_path or self.config_path
        with open(save_path, 'w', encoding='utf-8') as f:
            yaml.dump(self.config, f, default_flow_style=False, allow_unicode=True)
