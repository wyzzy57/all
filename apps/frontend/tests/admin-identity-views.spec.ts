import ElementPlus, { ElMessage, ElMessageBox } from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type UserRecord } from "@/api/client";
import { useAuthStore } from "@/stores/auth";
import AuthorizationView from "@/views/admin/AuthorizationView.vue";
import GroupManagementView from "@/views/admin/GroupManagementView.vue";
import UserManagementView from "@/views/admin/UserManagementView.vue";

const admin: UserRecord = {
  id: "admin-1",
  username: "admin",
  display_name: "Administrator",
  email: "admin@example.com",
  role: "admin",
  status: "active",
  must_change_password: false,
};

const member: UserRecord = {
  id: "u1",
  username: "member",
  display_name: "Member",
  email: "member@example.com",
  role: "member",
  status: "active",
  must_change_password: false,
};

function mountView(component: object) {
  const pinia = createPinia();
  setActivePinia(pinia);
  const auth = useAuthStore();
  auth.$patch({
    initialized: true,
    user: admin,
    accessToken: "admin-token",
    expiresAt: Date.now() + 60_000,
  });
  return { auth, wrapper: mount(component, { global: { plugins: [pinia, ElementPlus] } }) };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, reject, resolve };
}

function mockAuthorizationDependencies() {
  vi.spyOn(api, "listResourceGrants").mockResolvedValue({ items: [], total: 0 });
  vi.spyOn(api, "listResourceAllocations").mockResolvedValue({ items: [], total: 0 });
  vi.spyOn(api, "listUsers").mockResolvedValue({ items: [member], total: 1 });
  vi.spyOn(api, "listUserGroups").mockResolvedValue({ items: [], total: 0 });
  vi.spyOn(api, "listResourcePools").mockResolvedValue({ items: [], total: 0 });
}

describe("identity administration views", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("loads users with paging filters and submits the create form", async () => {
    const listUsers = vi.spyOn(api, "listUsers").mockResolvedValue({ items: [member], total: 1 });
    const createUser = vi.spyOn(api, "createUser").mockResolvedValue({ ...member, id: "u2" });
    const { wrapper } = mountView(UserManagementView);
    await flushPromises();

    expect(listUsers).toHaveBeenCalledWith(expect.objectContaining({ limit: 20, offset: 0 }));
    expect(wrapper.text()).toContain("member@example.com");

    await wrapper.get("[data-testid='create-user-button']").trigger("click");
    await wrapper.get("[data-testid='user-username']").setValue("new-member");
    await wrapper.get("[data-testid='user-display-name']").setValue("New Member");
    await wrapper.get("[data-testid='user-email']").setValue("new@example.com");
    expect(wrapper.get("[data-testid='submit-user']").attributes()).toMatchObject({ type: "submit", form: "user-editor-form" });
    await wrapper.get("[data-testid='user-editor-form']").trigger("submit");
    await flushPromises();

    expect(createUser).toHaveBeenCalledWith(expect.objectContaining({
      username: "new-member",
      display_name: "New Member",
      email: "new@example.com",
      role: "member",
    }));
  });

  it("prevents self disable/delete/role downgrade and hides edit for deleted users", async () => {
    const deleted = { ...member, id: "deleted-1", username: "deleted", status: "deleted" };
    vi.spyOn(api, "listUsers").mockResolvedValue({ items: [admin, member, deleted], total: 3 });
    const { wrapper } = mountView(UserManagementView);
    await flushPromises();

    expect(wrapper.find("[data-testid='toggle-user-admin-1']").exists()).toBe(false);
    expect(wrapper.find("[data-testid='delete-user-admin-1']").exists()).toBe(false);
    expect(wrapper.find("[data-testid='edit-user-deleted-1']").exists()).toBe(false);

    await wrapper.get("[data-testid='edit-user-admin-1']").trigger("click");
    expect(wrapper.get("[data-testid='user-role-member'] input").attributes("disabled")).toBeDefined();
  });

  it("confirms disabling and treats a cancelled reset as a no-op", async () => {
    vi.spyOn(api, "listUsers").mockResolvedValue({ items: [member], total: 1 });
    const updateUser = vi.spyOn(api, "updateUser").mockResolvedValue({ ...member, status: "disabled" });
    const resetPassword = vi.spyOn(api, "resetUserPassword").mockResolvedValue({
      user: member,
      temporary_password: "temporary",
    });
    const confirm = vi.spyOn(ElMessageBox, "confirm").mockResolvedValue(undefined as never);
    const { wrapper } = mountView(UserManagementView);
    await flushPromises();

    await wrapper.get("[data-testid='toggle-user-u1']").trigger("click");
    await flushPromises();
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining("禁用"), expect.any(String), expect.any(Object));
    expect(updateUser).toHaveBeenCalledWith("u1", { status: "disabled" });

    confirm.mockRejectedValueOnce("cancel");
    await wrapper.get("[data-testid='reset-user-u1']").trigger("click");
    await flushPromises();
    expect(resetPassword).not.toHaveBeenCalled();
  });

  it("keeps only the latest user-list response", async () => {
    const first = deferred<{ items: UserRecord[]; total: number }>();
    const second = deferred<{ items: UserRecord[]; total: number }>();
    vi.spyOn(api, "listUsers")
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    const { wrapper } = mountView(UserManagementView);

    await wrapper.get("[data-testid='user-search']").setValue("newer");
    await wrapper.get("[data-testid='user-search-submit']").trigger("click");
    second.resolve({ items: [{ ...member, id: "new", username: "newer" }], total: 1 });
    await flushPromises();
    expect(wrapper.text()).toContain("newer");

    first.resolve({ items: [{ ...member, id: "old", username: "stale" }], total: 1 });
    await flushPromises();
    expect(wrapper.text()).toContain("newer");
    expect(wrapper.text()).not.toContain("stale");
  });

  it("loads all member selector pages and replaces group membership", async () => {
    vi.spyOn(api, "listUserGroups").mockResolvedValue({
      items: [{ id: "g1", name: "研发组", description: null, status: "active", member_ids: ["u1"], member_count: 1 }],
      total: 1,
    });
    const listUsers = vi.spyOn(api, "listUsers").mockImplementation(async ({ offset = 0 } = {}) => (
      offset === 0
        ? { items: Array.from({ length: 200 }, (_, index) => ({ ...member, id: `u${index + 1}` })), total: 201 }
        : { items: [{ ...member, id: "u201", username: "last-member" }], total: 201 }
    ));
    vi.spyOn(api, "updateUserGroup").mockResolvedValue({
      id: "g1", name: "研发组", description: null, status: "active", member_ids: ["u1"], member_count: 1,
    });
    const replaceMembers = vi.spyOn(api, "replaceUserGroupMembers").mockResolvedValue({
      id: "g1", name: "研发组", description: null, status: "active", member_ids: [], member_count: 0,
    });
    const { wrapper } = mountView(GroupManagementView);
    await flushPromises();

    expect(listUsers).toHaveBeenCalledWith(expect.objectContaining({ limit: 200, offset: 200 }));
    await wrapper.get("[data-testid='edit-group-g1']").trigger("click");
    expect(wrapper.get("[data-testid='submit-group']").attributes()).toMatchObject({ type: "submit", form: "group-editor-form" });
    const checkbox = wrapper.get("[data-testid='group-member-u1']").get("input");
    await checkbox.setValue(false);
    await wrapper.get("[data-testid='group-editor-form']").trigger("submit");
    await flushPromises();

    expect(replaceMembers).toHaveBeenCalledWith("g1", []);
  });

  it("keeps a newly created group open for membership retry after partial success", async () => {
    vi.spyOn(api, "listUserGroups").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(api, "listUsers").mockResolvedValue({ items: [member], total: 1 });
    const createdGroup = { id: "g-new", name: "新分组", description: null, status: "active", member_ids: [], member_count: 0 };
    const createGroup = vi.spyOn(api, "createUserGroup").mockResolvedValue(createdGroup);
    const updateGroup = vi.spyOn(api, "updateUserGroup").mockResolvedValue(createdGroup);
    vi.spyOn(api, "replaceUserGroupMembers")
      .mockRejectedValueOnce(new Error("membership unavailable"))
      .mockResolvedValueOnce(createdGroup);
    const warning = vi.spyOn(ElMessage, "warning");
    const { wrapper } = mountView(GroupManagementView);
    await flushPromises();

    await wrapper.get("[data-testid='create-group-button']").trigger("click");
    await wrapper.get("[data-testid='group-name']").setValue("新分组");
    await wrapper.get("[data-testid='group-editor-form']").trigger("submit");
    await flushPromises();

    expect(createGroup).toHaveBeenCalledOnce();
    expect(warning).toHaveBeenCalledWith(expect.stringContaining("分组已创建，但成员同步失败"));
    expect(wrapper.text()).toContain("编辑分组");

    await wrapper.get("[data-testid='group-editor-form']").trigger("submit");
    await flushPromises();
    expect(createGroup).toHaveBeenCalledOnce();
    expect(updateGroup).toHaveBeenCalledWith("g-new", expect.any(Object));
  });

  it("keeps only the latest group page response", async () => {
    const first = deferred<{ items: never[]; total: number }>();
    const second = deferred<{ items: Array<{ id: string; name: string; description: null; status: string; member_ids: string[]; member_count: number }>; total: number }>();
    const listGroups = vi.spyOn(api, "listUserGroups")
      .mockReturnValueOnce(first.promise)
      .mockReturnValueOnce(second.promise);
    vi.spyOn(api, "listUsers").mockResolvedValue({ items: [], total: 0 });
    const { wrapper } = mountView(GroupManagementView);

    const pagination = wrapper.getComponent("[data-testid='group-pagination']") as VueWrapper;
    pagination.vm.$emit("update:current-page", 2);
    pagination.vm.$emit("current-change", 2);
    second.resolve({
      items: [{ id: "g-new", name: "当前页分组", description: null, status: "active", member_ids: [], member_count: 0 }],
      total: 21,
    });
    await flushPromises();
    expect(listGroups).toHaveBeenCalledWith({ limit: 20, offset: 20 });
    expect(wrapper.text()).toContain("当前页分组");

    first.resolve({ items: [], total: 1 });
    await flushPromises();
    expect(wrapper.text()).toContain("当前页分组");
  });

  it("treats cancelled group deletion as a no-op", async () => {
    vi.spyOn(api, "listUserGroups").mockResolvedValue({
      items: [{ id: "g1", name: "研发组", description: null, status: "active", member_ids: [], member_count: 0 }],
      total: 1,
    });
    vi.spyOn(api, "listUsers").mockResolvedValue({ items: [], total: 0 });
    const deleteGroup = vi.spyOn(api, "deleteUserGroup").mockResolvedValue();
    vi.spyOn(ElMessageBox, "confirm").mockRejectedValue("cancel");
    const { wrapper } = mountView(GroupManagementView);
    await flushPromises();

    await wrapper.get("[data-testid='delete-group-g1']").trigger("click");
    await flushPromises();
    expect(deleteGroup).not.toHaveBeenCalled();
  });

  it("loads authorization data independently and uses server-side paging", async () => {
    const grant = {
      id: "grant-1", resource_type: "dataset", resource_id: "dataset-1", principal_type: "user" as const,
      principal_id: "u1", permissions: ["view"], expires_at: null,
    };
    const listGrants = vi.spyOn(api, "listResourceGrants").mockResolvedValue({ items: [grant], total: 25 });
    vi.spyOn(api, "listResourceAllocations").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(api, "listUsers").mockRejectedValue(new Error("users unavailable"));
    vi.spyOn(api, "listUserGroups").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(api, "listResourcePools").mockResolvedValue({ items: [], total: 0 });
    const error = vi.spyOn(ElMessage, "error");
    const { wrapper } = mountView(AuthorizationView);
    await flushPromises();

    expect(wrapper.text()).toContain("dataset-1");
    expect(listGrants).toHaveBeenCalledWith({ limit: 20, offset: 0 });
    expect(error).toHaveBeenCalledWith(expect.stringContaining("用户选项加载失败"));

    await wrapper.findAll("[data-testid='grant-pagination'] .el-pager .number")[1].trigger("click");
    await flushPromises();
    expect(listGrants).toHaveBeenCalledWith({ limit: 20, offset: 20 });
  });

  it("loads selector users across pages and submits a resource grant form", async () => {
    mockAuthorizationDependencies();
    const listUsers = vi.spyOn(api, "listUsers").mockImplementation(async ({ offset = 0 } = {}) => (
      offset === 0
        ? { items: Array.from({ length: 200 }, (_, index) => ({ ...member, id: `u${index + 1}` })), total: 201 }
        : { items: [{ ...member, id: "u201" }], total: 201 }
    ));
    const upsertGrant = vi.spyOn(api, "upsertResourceGrant").mockResolvedValue({
      id: "grant-1", resource_type: "dataset", resource_id: "dataset-1", principal_type: "user",
      principal_id: "u1", permissions: ["view"], expires_at: null,
    });
    const { wrapper } = mountView(AuthorizationView);
    await flushPromises();

    expect(listUsers).toHaveBeenCalledWith(expect.objectContaining({ limit: 200, offset: 200 }));
    await wrapper.get("[data-testid='create-grant-button']").trigger("click");
    await wrapper.get("[data-testid='grant-resource-type']").setValue("dataset");
    await wrapper.get("[data-testid='grant-resource-id']").setValue("dataset-1");
    expect(wrapper.get("[data-testid='submit-grant']").attributes()).toMatchObject({ type: "submit", form: "grant-editor-form" });
    await wrapper.get("[data-testid='grant-editor-form']").trigger("submit");
    await flushPromises();

    expect(upsertGrant).toHaveBeenCalledWith(expect.objectContaining({
      resource_type: "dataset",
      resource_id: "dataset-1",
      principal_type: "user",
      principal_id: "u1",
      permissions: ["view"],
    }));
  });

  it("keeps independent latest grant and allocation page responses", async () => {
    const firstGrants = deferred<{ items: never[]; total: number }>();
    const secondGrants = deferred<{ items: Array<{ id: string; resource_type: string; resource_id: string; principal_type: "user"; principal_id: string; permissions: string[]; expires_at: null }>; total: number }>();
    const firstAllocations = deferred<{ items: never[]; total: number }>();
    const secondAllocations = deferred<{ items: Array<{ id: string; principal_type: "user"; principal_id: string; resource_pool_id: string; max_concurrent_training_jobs: number; max_gpu_count: number; max_service_instances: number; expires_at: null }>; total: number }>();
    const listGrants = vi.spyOn(api, "listResourceGrants")
      .mockReturnValueOnce(firstGrants.promise)
      .mockReturnValueOnce(secondGrants.promise);
    const listAllocations = vi.spyOn(api, "listResourceAllocations")
      .mockReturnValueOnce(firstAllocations.promise)
      .mockReturnValueOnce(secondAllocations.promise);
    vi.spyOn(api, "listUsers").mockResolvedValue({ items: [member], total: 1 });
    vi.spyOn(api, "listUserGroups").mockResolvedValue({ items: [], total: 0 });
    vi.spyOn(api, "listResourcePools").mockResolvedValue({ items: [], total: 0 });
    const { wrapper } = mountView(AuthorizationView);

    const grantPagination = wrapper.getComponent("[data-testid='grant-pagination']") as VueWrapper;
    grantPagination.vm.$emit("update:current-page", 2);
    grantPagination.vm.$emit("current-change", 2);
    const allocationPagination = wrapper.getComponent("[data-testid='allocation-pagination']") as VueWrapper;
    allocationPagination.vm.$emit("update:current-page", 2);
    allocationPagination.vm.$emit("current-change", 2);

    secondGrants.resolve({
      items: [{ id: "grant-new", resource_type: "dataset", resource_id: "current-grant", principal_type: "user", principal_id: "u1", permissions: ["view"], expires_at: null }],
      total: 21,
    });
    secondAllocations.resolve({
      items: [{ id: "allocation-new", principal_type: "user", principal_id: "current-allocation", resource_pool_id: "pool", max_concurrent_training_jobs: 1, max_gpu_count: 1, max_service_instances: 1, expires_at: null }],
      total: 21,
    });
    await flushPromises();
    expect(listGrants).toHaveBeenCalledWith({ limit: 20, offset: 20 });
    expect(listAllocations).toHaveBeenCalledWith({ limit: 20, offset: 20 });
    expect(wrapper.text()).toContain("current-grant");
    expect(wrapper.text()).toContain("current-allocation");

    firstGrants.resolve({ items: [], total: 1 });
    firstAllocations.resolve({ items: [], total: 1 });
    await flushPromises();
    expect(wrapper.text()).toContain("current-grant");
    expect(wrapper.text()).toContain("current-allocation");
  });

  it("treats cancelled authorization deletion as a no-op", async () => {
    mockAuthorizationDependencies();
    vi.mocked(api.listResourceGrants).mockResolvedValue({
      items: [{ id: "grant-1", resource_type: "dataset", resource_id: "d1", principal_type: "user", principal_id: "u1", permissions: ["view"], expires_at: null }],
      total: 1,
    });
    const deleteGrant = vi.spyOn(api, "deleteResourceGrant").mockResolvedValue();
    vi.spyOn(ElMessageBox, "confirm").mockRejectedValue("cancel");
    const { wrapper } = mountView(AuthorizationView);
    await flushPromises();

    await wrapper.get("[data-testid='delete-grant-grant-1']").trigger("click");
    await flushPromises();
    expect(deleteGrant).not.toHaveBeenCalled();
  });
});
