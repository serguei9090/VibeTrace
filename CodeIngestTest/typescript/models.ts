export interface UserConfig {
  id: string;
  role: string;
  active: boolean;
}

export class UserManager {
  private defaultRole: string;

  constructor(defaultRole = "member") {
    this.defaultRole = defaultRole;
  }

  public createUser(id: string): UserConfig {
    return {
      id,
      role: this.defaultRole,
      active: this.validateUser(id),
    };
  }

  public validateUser(id: string): boolean {
    return id.length > 3;
  }
}
